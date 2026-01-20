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
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import psutil
import gc
import yt_dlp

from config import config

logger = logging.getLogger(__name__)

class YouTubeUtils:
    """YouTube utilities with caching"""
    
    _url_patterns = None
    
    @classmethod
    def _get_url_patterns(cls):
        if cls._url_patterns is None:
            cls._url_patterns = [
                r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$',
                r'^https?://(www\.)?youtube\.com/watch\?v=[\w-]{11}',
                r'^https?://(www\.)?youtube\.com/embed/[\w-]{11}',
                r'^https?://(www\.)?youtube\.com/shorts/[\w-]{11}',
                r'^https?://(www\.)?youtube\.com/live/[\w-]{11}',
                r'^https?://youtu\.be/[\w-]{11}',
                r'^https?://(www\.)?youtube\.com/playlist\?list=([\w-]+)',
                r'^https?://(www\.)?music\.youtube\.com/watch\?v=[\w-]{11}',
                r'^https?://(www\.)?m\.youtube\.com/watch\?v=[\w-]{11}',
            ]
        return cls._url_patterns
    
    @staticmethod
    def is_valid_youtube_url(url: str) -> bool:
        patterns = YouTubeUtils._get_url_patterns()
        for pattern in patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        return False
    
    @staticmethod
    def extract_video_id(url: str) -> Optional[str]:
        patterns = [
            (r'(?:v=|\/)([\w-]{11})', 'v'),
            (r'embed\/([\w-]{11})', 'embed'),
            (r'shorts\/([\w-]{11})', 'shorts'),
            (r'live\/([\w-]{11})', 'live'),
            (r'youtu\.be\/([\w-]{11})', 'youtu.be'),
            (r'/([\w-]{11})\?', 'path_query'),
        ]
        
        for pattern, _ in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        try:
            parsed = urlparse(url)
            if parsed.hostname and any(domain in parsed.hostname for domain in ['youtube.com', 'youtu.be']):
                query_params = parse_qs(parsed.query)
                if 'v' in query_params and query_params['v'][0]:
                    return query_params['v'][0]
        except:
            pass
        
        return None
    
    @staticmethod
    def clean_title(title: str) -> str:
        if not title:
            return "untitled"
        
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            title = title.replace(char, '')
        
        title = re.sub(r'\s+', ' ', title).strip()
        
        if len(title) > 100:
            title = title[:97] + '...'
        
        return title
    
    @staticmethod
    def format_duration(seconds: int) -> str:
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
    async def search_youtube_async(query: str, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            from youtubesearchpython import VideosSearch
            
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                search = await loop.run_in_executor(
                    executor, 
                    lambda: VideosSearch(query, limit=limit)
                )
                data = await loop.run_in_executor(
                    executor,
                    lambda: search.result()
                )
            
            if data and "result" in data:
                videos = []
                for v in data["result"][:limit]:
                    if v and v.get("id"):
                        video_id = YouTubeUtils.extract_video_id(v.get("link", "")) or v.get("id")
                        videos.append({
                            'video_id': video_id,
                            'title': v.get("title", "No Title"),
                            'url': f"https://youtube.com/watch?v={video_id}",
                            'duration': YouTubeUtils.parse_duration_string(v.get("duration", "0:00")),
                            'duration_formatted': v.get("duration", "0:00"),
                            'thumbnail': v.get("thumbnails", [{}])[0].get("url") if v.get("thumbnails") else "",
                            'channel': v.get("channel", {}).get("name", "Unknown Channel"),
                            'view_count': v.get("viewCount", {}).get("text", "0 views") if isinstance(v.get("viewCount"), dict) else "0 views",
                            'upload_date': v.get("publishedTime", "Unknown"),
                        })
                return videos
        except ImportError:
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'default_search': f'ytsearch{limit}',
                    'skip_download': True,
                }
                
                if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
                    ydl_opts['cookiefile'] = config.COOKIES_FILE
                
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor() as executor:
                    info = await loop.run_in_executor(
                        executor,
                        lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(
                            f"ytsearch{limit}:{query}", download=False
                        )
                    )
                
                results = []
                for entry in info.get('entries', [])[:limit]:
                    if entry and entry.get('id'):
                        results.append({
                            'video_id': entry.get('id'),
                            'title': entry.get('title', 'No Title'),
                            'duration': entry.get('duration', 0),
                            'duration_formatted': YouTubeUtils.format_duration(entry.get('duration', 0)),
                            'thumbnail': entry.get('thumbnail'),
                            'channel': entry.get('channel'),
                            'view_count': entry.get('view_count'),
                            'upload_date': entry.get('upload_date'),
                            'url': f"https://youtube.com/watch?v={entry.get('id')}",
                        })
                return results
            except Exception as e:
                logger.error(f"yt-dlp search failed: {e}")
        except Exception as e:
            logger.error(f"Search error: {e}")
        
        return []
    
    @staticmethod
    def parse_duration_string(duration_str: str) -> int:
        if not duration_str:
            return 0
        
        try:
            parts = duration_str.split(':')
            
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            elif len(parts) == 1:
                try:
                    return int(parts[0])
                except:
                    numbers = re.findall(r'\d+', parts[0])
                    if numbers:
                        return int(numbers[0])
                    return 0
            else:
                return 0
        except:
            return 0
    
    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        if not size_bytes:
            return "0 B"
        
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"


class RateLimiter:
    """Rate limiter with sliding window"""
    
    def __init__(self):
        self.requests = {}
        self.lock = asyncio.Lock()
        self.stats = {
            'total_requests': 0,
            'blocked_requests': 0,
            'by_ip': {}
        }
    
    async def check_limit(self, client_ip: str) -> bool:
        async with self.lock:
            current_time = time.time()
            window_start = current_time - 60
            
            self.requests = {
                ip: [(ts, count) for ts, count in timestamps if ts > window_start]
                for ip, timestamps in self.requests.items()
                if any(ts > window_start for ts, _ in timestamps)
            }
            
            if client_ip not in self.requests:
                self.requests[client_ip] = []
            
            request_count = sum(count for ts, count in self.requests[client_ip] if ts > window_start)
            
            if request_count >= config.MAX_REQUESTS_PER_MINUTE:
                self.stats['blocked_requests'] += 1
                self.stats['by_ip'][client_ip] = self.stats['by_ip'].get(client_ip, 0) + 1
                return False
            
            self.requests[client_ip].append((current_time, 1))
            self.stats['total_requests'] += 1
            
            self.requests[client_ip] = [
                (ts, count) for ts, count in self.requests[client_ip] 
                if ts > window_start
            ]
            
            return True
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            'total_requests': self.stats['total_requests'],
            'blocked_requests': self.stats['blocked_requests'],
            'active_ips': len(self.requests),
            'by_ip': dict(list(self.stats['by_ip'].items())[:10])
        }
    
    async def reset_ip(self, client_ip: str):
        async with self.lock:
            if client_ip in self.requests:
                del self.requests[client_ip]


class Cache:
    """LRU cache with TTL"""
    
    def __init__(self):
        self.cache = {}
        self.lock = asyncio.Lock()
        self.access_times = {}
        self.stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'size_bytes': 0
        }
    
    async def get(self, key: str) -> Optional[Any]:
        async with self.lock:
            if key in self.cache:
                entry = self.cache[key]
                timestamp, value, size = entry
                
                if time.time() - timestamp < config.CACHE_TTL:
                    self.access_times[key] = time.time()
                    self.stats['hits'] += 1
                    return value
                else:
                    del self.cache[key]
                    del self.access_times[key]
                    self.stats['size_bytes'] -= size
                    self.stats['misses'] += 1
            else:
                self.stats['misses'] += 1
            return None
    
    async def set(self, key: str, value: Any, size: int = 0):
        async with self.lock:
            if size == 0:
                try:
                    size = len(str(value).encode('utf-8'))
                except:
                    size = 1024
            
            while len(self.cache) >= config.MAX_CACHE_SIZE:
                if self.access_times:
                    oldest_key = min(self.access_times.items(), key=lambda x: x[1])[0]
                    if oldest_key in self.cache:
                        old_size = self.cache[oldest_key][2]
                        del self.cache[oldest_key]
                        del self.access_times[oldest_key]
                        self.stats['size_bytes'] -= old_size
                        self.stats['evictions'] += 1
                else:
                    key_to_remove = next(iter(self.cache))
                    old_size = self.cache[key_to_remove][2]
                    del self.cache[key_to_remove]
                    if key_to_remove in self.access_times:
                        del self.access_times[key_to_remove]
                    self.stats['size_bytes'] -= old_size
                    self.stats['evictions'] += 1
            
            self.cache[key] = (time.time(), value, size)
            self.access_times[key] = time.time()
            self.stats['size_bytes'] += size
    
    async def delete(self, key: str):
        async with self.lock:
            if key in self.cache:
                size = self.cache[key][2]
                del self.cache[key]
                if key in self.access_times:
                    del self.access_times[key]
                self.stats['size_bytes'] -= size
    
    async def clear(self):
        async with self.lock:
            self.cache.clear()
            self.access_times.clear()
            self.stats['size_bytes'] = 0
            self.stats['evictions'] = 0
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            'size': len(self.cache),
            'hits': self.stats['hits'],
            'misses': self.stats['misses'],
            'hit_ratio': self.stats['hits'] / max(1, self.stats['hits'] + self.stats['misses']),
            'evictions': self.stats['evictions'],
            'memory_usage_mb': self.stats['size_bytes'] / (1024 * 1024),
            'max_size': config.MAX_CACHE_SIZE,
            'ttl_seconds': config.CACHE_TTL
        }


class SystemMonitor:
    """System resource monitor"""
    
    @staticmethod
    def get_system_stats() -> Dict[str, Any]:
        try:
            process = psutil.Process()
            
            memory = process.memory_info()
            cpu_percent = process.cpu_percent(interval=0.1)
            disk_usage = psutil.disk_usage('/')
            net_io = psutil.net_io_counters()
            
            gc_stats = {
                'objects': len(gc.get_objects()),
                'collected': gc.collect(),
                'threshold': gc.get_threshold(),
                'count': gc.get_count()
            }
            
            return {
                'memory': {
                    'rss_mb': memory.rss / (1024 * 1024),
                    'vms_mb': memory.vms / (1024 * 1024),
                    'percent': process.memory_percent()
                },
                'cpu': {
                    'percent': cpu_percent,
                    'threads': process.num_threads()
                },
                'disk': {
                    'total_gb': disk_usage.total / (1024**3),
                    'used_gb': disk_usage.used / (1024**3),
                    'free_gb': disk_usage.free / (1024**3),
                    'percent': disk_usage.percent
                },
                'network': {
                    'bytes_sent_mb': net_io.bytes_sent / (1024 * 1024),
                    'bytes_recv_mb': net_io.bytes_recv / (1024 * 1024)
                },
                'gc': gc_stats,
                'uptime_seconds': time.time() - process.create_time(),
                'open_files': len(process.open_files()),
                'connections': len(process.connections())
            }
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            return {'error': str(e)}


class YouTubeDownloader:
    """YouTube downloader with extraction strategies"""
    
    _executor = ThreadPoolExecutor(max_workers=4)
    
    @staticmethod
    def get_ydl_options(video_type: str = "video", quality: str = "best", 
                       use_cookies: bool = True) -> Dict[str, Any]:
        ydl_opts = config.YTDLP_DEFAULT_OPTS.copy()
        
        if use_cookies and config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        if config.PROXY:
            ydl_opts['proxy'] = config.PROXY
        
        if video_type == "audio":
            ydl_opts['format'] = (
                'bestaudio[ext=m4a]/'
                'bestaudio[ext=webm]/'
                'bestaudio[ext=opus]/'
                'bestaudio/best'
            )
            ydl_opts['postprocessors'] = []
            
            ydl_opts['extractor_args']['youtube'].update({
                'player_client': ['android', 'ios', 'web', 'tvhtml5_simple'],
                'player_skip': ['configs', 'webpage', 'js'],
                'skip': ['hls', 'dash'],
            })
            
        elif video_type == "video":
            quality_map = {
                "low": "best[height<=360][filesize<50M]",
                "medium": "best[height<=480][filesize<100M]",
                "high": "best[height<=720][filesize<200M]",
                "best": "best[height<=1080][filesize<500M]/best[height<=720]/best",
                "4k": "best[height<=2160][filesize<1G]/best[height<=1440]/best",
            }
            ydl_opts['format'] = quality_map.get(quality, quality_map["best"])
        
        ydl_opts['concurrent_fragment_downloads'] = 2
        ydl_opts['http_chunk_size'] = 1048576
        ydl_opts['no_resize_buffer'] = True
        ydl_opts['no_part'] = True
        
        return ydl_opts
    
    @classmethod
    async def get_stream_info(cls, url: str, video_type: str = "video", 
                            quality: str = "best") -> Dict[str, Any]:
        try:
            video_id = youtube_utils.extract_video_id(url)
            if not video_id:
                return {'status': 'error', 'message': 'Invalid YouTube URL'}
            
            cache_key = f"{video_id}:{video_type}:{quality}"
            cached_data = await cache.get(cache_key)
            if cached_data:
                logger.info(f"Cache hit for {video_id}")
                return cached_data
            
            logger.info(f"Processing: {video_id} | Type: {video_type} | Quality: {quality}")
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                cls._executor,
                lambda: cls._extract_info_sync(url, video_type, quality)
            )
            
            if result.get('status') == 'success':
                await cache.set(cache_key, result, size=10240)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting stream info: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'video_id': youtube_utils.extract_video_id(url) or 'unknown'
            }
    
    @staticmethod
    def _extract_info_sync(url: str, video_type: str, quality: str) -> Dict[str, Any]:
        try:
            video_id = YouTubeUtils.extract_video_id(url)
            
            ydl_opts = YouTubeDownloader.get_ydl_options(video_type, quality, use_cookies=True)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    ydl_opts = YouTubeDownloader.get_ydl_options(video_type, quality, use_cookies=False)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_no_cookies:
                        info = ydl_no_cookies.extract_info(url, download=False)
                
                if not info:
                    return {'status': 'error', 'message': 'Could not extract video info'}
                
                result = {
                    'status': 'success',
                    'video_id': video_id,
                    'title': info.get('title', 'Unknown Title'),
                    'duration': info.get('duration', 0),
                    'duration_formatted': YouTubeUtils.format_duration(info.get('duration', 0)),
                    'thumbnail': info.get('thumbnail', ''),
                    'channel': info.get('channel', 'Unknown Channel'),
                    'channel_id': info.get('channel_id', ''),
                    'view_count': info.get('view_count', 0),
                    'like_count': info.get('like_count', 0),
                    'upload_date': info.get('upload_date', ''),
                    'categories': info.get('categories', []),
                    'tags': info.get('tags', [])[:10],
                    'description': (info.get('description', '')[:200] + '...') 
                                  if len(info.get('description', '')) > 200 
                                  else info.get('description', ''),
                    'age_limit': info.get('age_limit', 0),
                    'is_live': info.get('is_live', False),
                    'live_status': info.get('live_status', 'not_live'),
                }
                
                formats = info.get('formats', [])
                
                if video_type == "audio":
                    audio_formats = [f for f in formats if f.get('acodec') != 'none']
                    
                    if audio_formats:
                        audio_formats.sort(
                            key=lambda x: (
                                x.get('abr', 0) or x.get('tbr', 0) or 0,
                                1 if 'm4a' in str(x.get('ext', '')).lower() else 0,
                                1 if 'mp3' in str(x.get('ext', '')).lower() else 0,
                                x.get('asr', 0) or 0
                            ),
                            reverse=True
                        )
                        
                        best_audio = audio_formats[0]
                        result.update({
                            'stream_url': best_audio['url'],
                            'type': 'audio',
                            'format': {
                                'ext': best_audio.get('ext', 'm4a'),
                                'abr': best_audio.get('abr', 128),
                                'asr': best_audio.get('asr', 44100),
                                'acodec': best_audio.get('acodec', 'none'),
                                'filesize': best_audio.get('filesize'),
                                'filesize_formatted': YouTubeUtils.format_file_size(
                                    best_audio.get('filesize') or 0
                                ),
                                'protocol': best_audio.get('protocol', ''),
                                'format_note': best_audio.get('format_note', ''),
                                'quality': best_audio.get('quality', 0)
                            }
                        })
                    else:
                        return {'status': 'error', 'message': 'No audio format found'}
                
                else:
                    video_formats = [f for f in formats if f.get('vcodec') != 'none']
                    
                    if quality == "low":
                        video_formats = [f for f in video_formats 
                                       if f.get('height', 0) <= 360]
                    elif quality == "medium":
                        video_formats = [f for f in video_formats 
                                       if f.get('height', 0) <= 480]
                    elif quality == "high":
                        video_formats = [f for f in video_formats 
                                       if f.get('height', 0) <= 720]
                    
                    video_formats.sort(
                        key=lambda x: (
                            x.get('height', 0) or 0,
                            x.get('width', 0) or 0,
                            x.get('fps', 0) or 0,
                            x.get('tbr', 0) or 0
                        ),
                        reverse=True
                    )
                    
                    if video_formats:
                        best_video = video_formats[0]
                        result.update({
                            'stream_url': best_video['url'],
                            'type': 'video',
                            'format': {
                                'ext': best_video.get('ext', 'mp4'),
                                'height': best_video.get('height'),
                                'width': best_video.get('width'),
                                'fps': best_video.get('fps'),
                                'vcodec': best_video.get('vcodec', 'none'),
                                'acodec': best_video.get('acodec', 'none'),
                                'filesize': best_video.get('filesize'),
                                'filesize_formatted': YouTubeUtils.format_file_size(
                                    best_video.get('filesize') or 0
                                ),
                                'format_note': best_video.get('format_note', ''),
                                'protocol': best_video.get('protocol', '')
                            }
                        })
                    else:
                        return {'status': 'error', 'message': f'No {quality} quality video format found'}
                
                if video_type == "video":
                    result['available_qualities'] = cls._get_available_qualities(formats)
                
                return result
                
        except yt_dlp.utils.DownloadError as e:
            if "Private video" in str(e):
                return {'status': 'error', 'message': 'Video is private'}
            elif "Members-only" in str(e):
                return {'status': 'error', 'message': 'Video is members-only'}
            elif "Copyright" in str(e):
                return {'status': 'error', 'message': 'Video blocked by copyright'}
            else:
                return {'status': 'error', 'message': f'Download error: {str(e)}'}
        except Exception as e:
            return {'status': 'error', 'message': f'Extraction error: {str(e)}'}
    
    @staticmethod
    def _get_available_qualities(formats: List[Dict]) -> List[Dict]:
        qualities = {}
        for fmt in formats:
            if fmt.get('vcodec') != 'none' and fmt.get('height'):
                height = fmt['height']
                if height not in qualities:
                    qualities[height] = {
                        'height': height,
                        'width': fmt.get('width'),
                        'fps': fmt.get('fps'),
                        'ext': fmt.get('ext'),
                        'filesize': fmt.get('filesize'),
                        'filesize_formatted': YouTubeUtils.format_file_size(fmt.get('filesize') or 0),
                        'format_note': fmt.get('format_note', ''),
                        'vcodec': fmt.get('vcodec'),
                        'acodec': fmt.get('acodec'),
                    }
        
        sorted_qualities = sorted(qualities.values(), key=lambda x: x['height'], reverse=True)
        
        for q in sorted_qualities:
            height = q['height']
            if height >= 2160:
                q['quality_label'] = '4K'
            elif height >= 1440:
                q['quality_label'] = '1440p'
            elif height >= 1080:
                q['quality_label'] = '1080p'
            elif height >= 720:
                q['quality_label'] = '720p'
            elif height >= 480:
                q['quality_label'] = '480p'
            elif height >= 360:
                q['quality_label'] = '360p'
            elif height >= 240:
                q['quality_label'] = '240p'
            elif height >= 144:
                q['quality_label'] = '144p'
            else:
                q['quality_label'] = f'{height}p'
        
        return sorted_qualities
    
    @staticmethod
    def _get_quality_label(height: int) -> str:
        if height >= 2160:
            return '4K'
        elif height >= 1440:
            return '1440p'
        elif height >= 1080:
            return '1080p'
        elif height >= 720:
            return '720p'
        elif height >= 480:
            return '480p'
        elif height >= 360:
            return '360p'
        elif height >= 240:
            return '240p'
        elif height >= 144:
            return '144p'
        else:
            return f'{height}p'


# Global instances
youtube_utils = YouTubeUtils()
rate_limiter = RateLimiter()
cache = Cache()
system_monitor = SystemMonitor()
downloader = YouTubeDownloader()