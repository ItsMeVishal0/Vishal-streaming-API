import os
import re
import time
import hashlib
import json
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse, parse_qs
import aiohttp
from concurrent.futures import ThreadPoolExecutor
import psutil
import gc
import yt_dlp

from config import config

logger = logging.getLogger(__name__)

class YouTubeUtils:
    """YouTube utilities"""
    
    @staticmethod
    def is_valid_youtube_url(url: str) -> bool:
        """Check if URL is a valid YouTube URL"""
        patterns = [
            r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$',
            r'^https?://(www\.)?youtube\.com/watch\?v=[\w-]{11}',
            r'^https?://youtu\.be/[\w-]{11}',
            r'^https?://(www\.)?youtube\.com/embed/[\w-]{11}',
            r'^https?://(www\.)?youtube\.com/shorts/[\w-]{11}',
        ]
        
        for pattern in patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        return False
    
    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        """Extract video ID from URL"""
        # Clean URL - remove ?si parameters
        if "?si=" in url:
            url = url.split("?si=")[0]
        
        patterns = [
            r'(?:v=|\/)([\w-]{11})',
            r'embed\/([\w-]{11})',
            r'shorts\/([\w-]{11})',
            r'youtu\.be\/([\w-]{11})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # If it's just an 11-character ID
        if re.match(r'^[\w-]{11}$', url):
            return url
        
        return None
    
    @staticmethod
    def clean_title(title: str) -> str:
        """Clean video title for filename"""
        if not title:
            return "video"
        
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            title = title.replace(char, '')
        
        title = re.sub(r'\s+', ' ', title).strip()
        
        if len(title) > 100:
            title = title[:97] + '...'
        
        return title
    
    @staticmethod
    def format_duration(seconds: int) -> str:
        """Format duration in seconds to HH:MM:SS or MM:SS"""
        if not seconds:
            return "0:00"
        
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"
    
    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        """Format file size in human readable format"""
        if not size_bytes:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"


class RateLimiter:
    """Simple rate limiter"""
    
    def __init__(self):
        self.requests = {}
        self.lock = asyncio.Lock()
    
    async def check_limit(self, client_ip: str) -> bool:
        """Check if client is within rate limits"""
        async with self.lock:
            current_time = time.time()
            window_start = current_time - 60
            
            # Clean old entries
            if client_ip in self.requests:
                self.requests[client_ip] = [
                    ts for ts in self.requests[client_ip] 
                    if ts > window_start
                ]
            
            # Get or create request list for this IP
            if client_ip not in self.requests:
                self.requests[client_ip] = []
            
            # Count requests in window
            request_count = len([ts for ts in self.requests[client_ip] if ts > window_start])
            
            # Check limit
            if request_count >= config.MAX_REQUESTS_PER_MINUTE:
                return False
            
            # Add new request
            self.requests[client_ip].append(current_time)
            return True


class Cache:
    """Simple cache"""
    
    def __init__(self):
        self.cache = {}
        self.lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        async with self.lock:
            if key in self.cache:
                timestamp, value = self.cache[key]
                if time.time() - timestamp < config.CACHE_TTL:
                    return value
                else:
                    del self.cache[key]
            return None
    
    async def set(self, key: str, value: Any):
        """Set value in cache"""
        async with self.lock:
            # Check if we need to evict
            while len(self.cache) >= config.MAX_CACHE_SIZE:
                # Remove oldest
                oldest_key = min(self.cache.items(), key=lambda x: x[1][0])[0]
                del self.cache[oldest_key]
            
            self.cache[key] = (time.time(), value)
    
    async def clear(self):
        """Clear all cache"""
        async with self.lock:
            self.cache.clear()


class YouTubeDownloader:
    """YouTube downloader with multiple extraction methods"""
    
    _executor = ThreadPoolExecutor(max_workers=4)
    
    @staticmethod
    def get_ydl_options(video_type: str = "video", quality: str = "best") -> Dict[str, Any]:
        """Get yt-dlp options"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 30,
            'retries': 3,
            'ignoreerrors': True,
            'no_color': True,
        }
        
        # Add cookies if available
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        # Add proxy if configured
        if config.PROXY:
            ydl_opts['proxy'] = config.PROXY
        
        # Format selection
        if video_type == "audio":
            ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
        else:
            if quality == "low":
                ydl_opts['format'] = 'best[height<=360]'
            elif quality == "medium":
                ydl_opts['format'] = 'best[height<=480]'
            elif quality == "high":
                ydl_opts['format'] = 'best[height<=720]'
            else:
                ydl_opts['format'] = 'best[height<=1080]/best'
        
        return ydl_opts
    
    @classmethod
    async def get_stream_info(cls, url: str, video_type: str = "video", 
                            quality: str = "best") -> Dict[str, Any]:
        """Get streaming information"""
        try:
            # Clean and validate URL
            video_id = YouTubeUtils.extract_video_id(url)
            if not video_id:
                return {'status': 'error', 'message': 'Invalid YouTube URL or ID'}
            
            # Check cache
            cache_key = f"{video_id}:{video_type}:{quality}"
            cached_data = await cache.get(cache_key)
            if cached_data:
                logger.info(f"Cache hit for {video_id}")
                return cached_data
            
            logger.info(f"Processing: {video_id} | Type: {video_type} | Quality: {quality}")
            
            # Build proper URL
            if not url.startswith(("http://", "https://")):
                if len(url) == 11:
                    url = f"https://youtu.be/{url}"
                else:
                    url = f"https://youtube.com/watch?v={url}"
            
            # Run yt-dlp
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                cls._executor,
                lambda: cls._extract_info_sync(url, video_type, quality)
            )
            
            # Cache successful results
            if result.get('status') == 'success':
                await cache.set(cache_key, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting stream info: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'video_id': YouTubeUtils.extract_video_id(url) or 'unknown'
            }
    
    @staticmethod
    def _extract_info_sync(url: str, video_type: str, quality: str) -> Dict[str, Any]:
        """Synchronous yt-dlp extraction"""
        try:
            video_id = YouTubeUtils.extract_video_id(url)
            
            # Try with cookies first
            ydl_opts = YouTubeDownloader.get_ydl_options(video_type, quality)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    # Try without cookies
                    ydl_opts_no_cookies = YouTubeDownloader.get_ydl_options(video_type, quality)
                    if 'cookiefile' in ydl_opts_no_cookies:
                        del ydl_opts_no_cookies['cookiefile']
                    
                    with yt_dlp.YoutubeDL(ydl_opts_no_cookies) as ydl_no_cookies:
                        info = ydl_no_cookies.extract_info(url, download=False)
                
                if not info:
                    return {'status': 'error', 'message': 'Could not extract video info'}
                
                # Get stream URL
                stream_url = None
                
                if 'url' in info:
                    stream_url = info['url']
                elif 'formats' in info and info['formats']:
                    formats = info['formats']
                    
                    if video_type == "audio":
                        # Find audio formats
                        audio_formats = [f for f in formats if f.get('acodec') != 'none']
                        if audio_formats:
                            # Sort by bitrate
                            audio_formats.sort(key=lambda x: x.get('abr', 0) or x.get('tbr', 0) or 0, reverse=True)
                            stream_url = audio_formats[0]['url']
                    else:
                        # Find video formats
                        video_formats = [f for f in formats if f.get('vcodec') != 'none']
                        if video_formats:
                            # Filter by quality if specified
                            if quality == "low":
                                video_formats = [f for f in video_formats if f.get('height', 0) <= 360]
                            elif quality == "medium":
                                video_formats = [f for f in video_formats if f.get('height', 0) <= 480]
                            elif quality == "high":
                                video_formats = [f for f in video_formats if f.get('height', 0) <= 720]
                            
                            if not video_formats:
                                video_formats = [f for f in formats if f.get('vcodec') != 'none']
                            
                            # Sort by resolution
                            video_formats.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
                            stream_url = video_formats[0]['url']
                
                if not stream_url:
                    return {'status': 'error', 'message': 'No stream URL found'}
                
                # Build result
                result = {
                    'status': 'success',
                    'video_id': video_id,
                    'title': info.get('title', 'Unknown Title'),
                    'duration': info.get('duration', 0),
                    'duration_formatted': YouTubeUtils.format_duration(info.get('duration', 0)),
                    'thumbnail': info.get('thumbnail', ''),
                    'channel': info.get('channel', 'Unknown Channel'),
                    'view_count': info.get('view_count', 0),
                    'stream_url': stream_url,
                    'type': video_type,
                }
                
                # Add format info
                if video_type == "audio":
                    result['format'] = {
                        'ext': 'm4a',
                        'type': 'audio',
                    }
                else:
                    # Try to get video resolution
                    height = 0
                    if 'formats' in info and info['formats']:
                        for fmt in info['formats']:
                            if fmt.get('url') == stream_url and fmt.get('height'):
                                height = fmt.get('height')
                                break
                    
                    result['format'] = {
                        'ext': 'mp4',
                        'height': height,
                        'type': 'video',
                    }
                
                return result
                
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if "Private video" in error_msg:
                return {'status': 'error', 'message': 'Video is private'}
            elif "Members-only" in error_msg:
                return {'status': 'error', 'message': 'Video is members-only'}
            elif "age restricted" in error_msg.lower():
                return {'status': 'error', 'message': 'Video is age-restricted. Cookies required.'}
            elif "Copyright" in error_msg:
                return {'status': 'error', 'message': 'Video blocked by copyright'}
            else:
                return {'status': 'error', 'message': f'YouTube error: {error_msg}'}
        except Exception as e:
            return {'status': 'error', 'message': f'Extraction error: {str(e)}'}


# Global instances
youtube_utils = YouTubeUtils()
rate_limiter = RateLimiter()
cache = Cache()
downloader = YouTubeDownloader()