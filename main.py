import os
import sys
import time
import logging
import hashlib
import asyncio
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

import yt_dlp
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    StreamingResponse, 
    RedirectResponse, 
    JSONResponse, 
    FileResponse,
    HTMLResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
import aiohttp
from aiohttp import ClientTimeout

sys.path.append('.')

from config import config
from utils import youtube_utils, rate_limiter, cache, system_monitor, downloader

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Global request tracking
_request_stats = {
    'total_requests': 0,
    'requests_by_endpoint': {},
    'requests_by_ip': {},
    'start_time': time.time(),
    'last_cleanup': time.time()
}

security = HTTPBearer(auto_error=False)

def cleanup_old_stats():
    current_time = time.time()
    if current_time - _request_stats['last_cleanup'] > 3600:
        if len(_request_stats['requests_by_ip']) > 1000:
            _request_stats['requests_by_ip'] = dict(
                list(_request_stats['requests_by_ip'].items())[:1000]
            )
        _request_stats['last_cleanup'] = current_time

def track_request(endpoint: str, client_ip: str):
    _request_stats['total_requests'] += 1
    _request_stats['requests_by_endpoint'][endpoint] = _request_stats['requests_by_endpoint'].get(endpoint, 0) + 1
    _request_stats['requests_by_ip'][client_ip] = _request_stats['requests_by_ip'].get(client_ip, 0) + 1
    cleanup_old_stats()

# Lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 YouTube Streaming API Server starting...")
    logger.info(f"📁 Download directory: {config.DOWNLOAD_DIR}")
    logger.info(f"🌐 Server will run on: http://{config.HOST}:{config.PORT}")
    logger.info(f"🔧 Debug mode: {config.DEBUG}")
    
    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        logger.info("🍪 Cookies file detected")
    else:
        logger.warning("⚠️  No cookies.txt file found")
    
    if config.PROXY:
        logger.info(f"🌐 Proxy configured: {config.PROXY}")
    
    app.state.http_session = aiohttp.ClientSession(
        timeout=ClientTimeout(total=60),
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    
    try:
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            logger.info(f"✅ yt-dlp test successful: {info.get('title', 'Test video')[:50]}...")
    except Exception as e:
        logger.error(f"❌ yt-dlp test failed: {e}")
    
    yield
    
    logger.info("👋 Shutting down YouTube Streaming API Server...")
    
    if hasattr(app.state, 'http_session'):
        await app.state.http_session.close()

def get_fastapi_description(config):
    return f"""
Advanced YouTube Streaming API for audio and video streaming.

## Features
- 🎵 Audio streaming with multiple quality options
- 🎬 Video streaming up to 1080p
- 📥 Direct downloads
- 🔍 YouTube search
- 📊 Detailed video information
- 🚀 High performance with caching
- 🔒 Rate limiting
- 🏥 Health monitoring
- 📈 System statistics

## Rate Limits
- {config.MAX_REQUESTS_PER_MINUTE} requests per minute per IP
- {config.RATE_LIMIT_WINDOW} second window

## Cache
- {config.MAX_CACHE_SIZE} entries max
- {config.CACHE_TTL // 3600} hour TTL

Hosted on Render.com
"""

app = FastAPI(
    title="YouTube Streaming API",
    version="4.0.0",
    description=get_fastapi_description(config),
    docs_url=None,
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    openapi_schema["servers"] = [
        {
            "url": "https://your-app-name.onrender.com",
            "description": "Production server"
        },
        {
            "url": "http://localhost:8000",
            "description": "Local development"
        }
    ]
    
    openapi_schema["components"]["securitySchemes"] = {
        "Bearer": {
            "type": "http",
            "scheme": "bearer",
            "description": "Optional API key for admin endpoints"
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - Documentation",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png"
    )

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.url.path
    
    track_request(endpoint, client_ip)
    
    if not await rate_limiter.check_limit(client_ip):
        logger.warning(f"Rate limit exceeded for {client_ip} on {endpoint}")
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "message": f"Maximum {config.MAX_REQUESTS_PER_MINUTE} requests per minute allowed",
                "retry_after": 60,
                "limits": {
                    "max_requests": config.MAX_REQUESTS_PER_MINUTE,
                    "window_seconds": 60
                }
            },
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(config.MAX_REQUESTS_PER_MINUTE),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time() + 60))
            }
        )
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        response.headers["X-Process-Time"] = f"{process_time:.3f}s"
        response.headers["X-Cache-Hit"] = str(response.headers.get("X-Cache-Hit", "false"))
        
        response.headers["X-RateLimit-Limit"] = str(config.MAX_REQUESTS_PER_MINUTE)
        response.headers["X-RateLimit-Remaining"] = str(
            config.MAX_REQUESTS_PER_MINUTE - 
            len([ts for ts in rate_limiter.requests.get(client_ip, []) 
                 if time.time() - ts[0] < 60])
        )
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + 60))
        
        if process_time > 5.0:
            logger.warning(f"Slow request: {request.method} {endpoint} - {process_time:.3f}s")
        
        logger.info(f"{request.method} {endpoint} - {response.status_code} - {process_time:.3f}s")
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Error in {endpoint}: {e} - {process_time:.3f}s")
        raise

app.mount("/static", StaticFiles(directory="static"), name="static")

def get_content_type(ext: str, content_type: str = None) -> str:
    content_types = {
        'mp3': 'audio/mpeg',
        'm4a': 'audio/mp4',
        'webm': 'audio/webm',
        'ogg': 'audio/ogg',
        'opus': 'audio/ogg',
        'flac': 'audio/flac',
        'wav': 'audio/wav',
        'aac': 'audio/aac',
        'mp4': 'video/mp4',
        'webm': 'video/webm',
        'mkv': 'video/x-matroska',
        'avi': 'video/x-msvideo',
        'mov': 'video/quicktime',
        'flv': 'video/x-flv',
        '3gp': 'video/3gpp',
        'json': 'application/json',
        'txt': 'text/plain',
        'html': 'text/html',
        'css': 'text/css',
        'js': 'application/javascript',
        'pdf': 'application/pdf',
    }
    
    if content_type:
        return content_type
    
    ext = ext.lower().lstrip('.')
    return content_types.get(ext, 'application/octet-stream')

async def stream_file_generator(url: str, chunk_size: int = 8192):
    try:
        async with app.state.http_session.get(url) as response:
            response.raise_for_status()
            
            async for chunk in response.content.iter_chunked(chunk_size):
                yield chunk
                
    except aiohttp.ClientError as e:
        logger.error(f"Error streaming from {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Stream error: {str(e)}")

# API Endpoints
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Streaming API</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .container { max-width: 800px; margin: 0 auto; }
            .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
            .method { background: #4CAF50; color: white; padding: 3px 8px; border-radius: 3px; font-size: 12px; }
            .url { font-family: monospace; background: #eee; padding: 5px; margin: 5px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>YouTube Streaming API</h1>
            <p>Advanced API for streaming YouTube audio and video</p>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/stream/audio?url=YOUTUBE_URL</div>
                <p>Stream audio from YouTube video</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/stream/video?url=YOUTUBE_URL&quality=best</div>
                <p>Stream video with quality options</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/download/audio?url=YOUTUBE_URL</div>
                <p>Download audio file</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/download/video?url=YOUTUBE_URL&quality=best</div>
                <p>Download video file</p>
            </div>
            
            <p><a href="/docs">API Documentation</a> | <a href="/health">Health Check</a></p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api")
async def api_info():
    return {
        "service": "YouTube Streaming API",
        "version": "4.0.0",
        "status": "active",
        "endpoints": {
            "audio_stream": "/stream/audio?url=YOUTUBE_URL",
            "video_stream": "/stream/video?url=YOUTUBE_URL&quality=best",
            "audio_download": "/download/audio?url=YOUTUBE_URL",
            "video_download": "/download/video?url=YOUTUBE_URL&quality=best",
            "video_info": "/info?url=YOUTUBE_URL",
            "search": "/search?q=QUERY",
            "health": "/health",
            "stats": "/stats"
        }
    }

@app.get("/stream/audio")
async def stream_audio(
    request: Request,
    url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("best", description="Audio quality"),
    download: bool = Query(False, description="Force download"),
    force_refresh: bool = Query(False, description="Bypass cache")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    try:
        video_id = youtube_utils.extract_video_id(url)
        
        cache_key = f"audio:{video_id}:{quality}"
        if not force_refresh:
            cached_result = await cache.get(cache_key)
            if cached_result and cached_result.get('status') == 'success':
                logger.info(f"Cache hit for audio: {video_id}")
                result = cached_result
                result['cached'] = True
            else:
                result = await downloader.get_stream_info(url, "audio", quality)
        else:
            result = await downloader.get_stream_info(url, "audio", quality)
        
        if result['status'] != 'success':
            error_msg = result.get('message', 'Unknown error')
            raise HTTPException(status_code=500, detail=f"Audio extraction failed: {error_msg}")
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'audio'))
        ext = result.get('format', {}).get('ext', 'm4a')
        content_type = get_content_type(ext, 'audio/mpeg')
        
        logger.info(f"Audio stream: {title} | Format: {ext}")
        
        if result.get('status') == 'success' and not result.get('cached'):
            await cache.set(cache_key, result, size=10240)
        
        if download:
            filename = f"{title}.{ext}"
            
            return StreamingResponse(
                stream_file_generator(stream_url),
                media_type=content_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=86400',
                    'Content-Type': content_type,
                    'X-Audio-Title': title,
                    'X-Video-Id': video_id,
                    'X-Cache-Hit': str(result.get('cached', False)).lower()
                }
            )
        else:
            response = RedirectResponse(url=stream_url, status_code=302)
            
            response.headers.update({
                'Accept-Ranges': 'bytes',
                'Content-Type': content_type,
                'Cache-Control': 'public, max-age=86400',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Expose-Headers': '*',
                'X-Audio-Title': title,
                'X-Video-Id': video_id,
                'X-Stream-Url-Hash': hashlib.md5(stream_url.encode()).hexdigest()[:8],
                'X-Cache-Hit': str(result.get('cached', False)).lower()
            })
            
            return response
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio stream error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Audio streaming error: {str(e)}")

@app.get("/stream/video")
async def stream_video(
    request: Request,
    url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("best", description="Video quality: low, medium, high, best, 4k"),
    download: bool = Query(False, description="Force download"),
    force_refresh: bool = Query(False, description="Bypass cache")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    valid_qualities = ["low", "medium", "high", "best", "4k"]
    if quality not in valid_qualities:
        raise HTTPException(status_code=400, detail=f"Invalid quality. Must be one of: {', '.join(valid_qualities)}")
    
    try:
        video_id = youtube_utils.extract_video_id(url)
        
        cache_key = f"video:{video_id}:{quality}"
        if not force_refresh:
            cached_result = await cache.get(cache_key)
            if cached_result and cached_result.get('status') == 'success':
                logger.info(f"Cache hit for video: {video_id} - {quality}")
                result = cached_result
                result['cached'] = True
            else:
                result = await downloader.get_stream_info(url, "video", quality)
        else:
            result = await downloader.get_stream_info(url, "video", quality)
        
        if result['status'] != 'success':
            error_msg = result.get('message', 'Unknown error')
            raise HTTPException(status_code=500, detail=f"Video extraction failed: {error_msg}")
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'video'))
        ext = result.get('format', {}).get('ext', 'mp4')
        content_type = get_content_type(ext, 'video/mp4')
        height = result.get('format', {}).get('height', 'N/A')
        
        logger.info(f"Video stream: {title} | Quality: {quality} ({height}p)")
        
        if result.get('status') == 'success' and not result.get('cached'):
            await cache.set(cache_key, result, size=20480)
        
        if download:
            filename = f"{title}_{height}p.{ext}"
            
            return StreamingResponse(
                stream_file_generator(stream_url, chunk_size=131072),
                media_type=content_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'Accept-Ranges': 'bytes',
                    'Cache-Control': 'public, max-age=86400',
                    'Content-Type': content_type,
                    'X-Video-Title': title,
                    'X-Video-Id': video_id,
                    'X-Video-Quality': f"{height}p",
                    'X-Cache-Hit': str(result.get('cached', False)).lower()
                }
            )
        else:
            response = RedirectResponse(url=stream_url, status_code=302)
            
            response.headers.update({
                'Accept-Ranges': 'bytes',
                'Content-Type': content_type,
                'Cache-Control': 'public, max-age=7200',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Expose-Headers': '*',
                'X-Video-Title': title,
                'X-Video-Id': video_id,
                'X-Video-Quality': f"{height}p",
                'X-Stream-Url-Hash': hashlib.md5(stream_url.encode()).hexdigest()[:8],
                'X-Cache-Hit': str(result.get('cached', False)).lower()
            })
            
            return response
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video stream error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Video streaming error: {str(e)}")

@app.get("/info")
async def get_video_info(
    url: str = Query(..., description="YouTube video URL"),
    detailed: bool = Query(False, description="Include detailed information"),
    force_refresh: bool = Query(False, description="Bypass cache")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    try:
        video_id = youtube_utils.extract_video_id(url)
        
        cache_key = f"info:{video_id}:{detailed}"
        if not force_refresh:
            cached_info = await cache.get(cache_key)
            if cached_info:
                logger.info(f"Cache hit for info: {video_id}")
                cached_info['cached'] = True
                return cached_info
        
        result = await downloader.get_stream_info(url, "video", "best")
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Info extraction failed'))
        
        video_info = {
            'video_id': video_id,
            'title': result.get('title'),
            'description': result.get('description', ''),
            'duration': result.get('duration'),
            'duration_formatted': result.get('duration_formatted'),
            'thumbnail': result.get('thumbnail'),
            'channel': result.get('channel'),
            'channel_id': result.get('channel_id'),
            'view_count': result.get('view_count'),
            'like_count': result.get('like_count'),
            'upload_date': result.get('upload_date'),
            'categories': result.get('categories', []),
            'tags': result.get('tags', []),
            'age_limit': result.get('age_limit', 0),
            'is_live': result.get('is_live', False),
            'live_status': result.get('live_status', 'not_live'),
            'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
            'available_qualities': result.get('available_qualities', []),
            'timestamp': time.time()
        }
        
        if detailed:
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                    'writeinfojson': False,
                    'getcomments': False,
                }
                
                if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
                    ydl_opts['cookiefile'] = config.COOKIES_FILE
                
                loop = asyncio.get_event_loop()
                with downloader._executor as executor:
                    info = await loop.run_in_executor(
                        executor,
                        lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False)
                    )
                
                if info:
                    video_info.update({
                        'average_rating': info.get('average_rating'),
                        'description_full': info.get('description'),
                        'playlist': info.get('playlist'),
                        'playlist_index': info.get('playlist_index'),
                        'subtitles': list(info.get('subtitles', {}).keys()) if info.get('subtitles') else [],
                        'automatic_captions': list(info.get('automatic_captions', {}).keys()) if info.get('automatic_captions') else [],
                        'chapters': info.get('chapters'),
                        'comment_count': info.get('comment_count'),
                        'webpage_url_direct': info.get('webpage_url'),
                        'original_url': info.get('original_url'),
                    })
                    
                    thumbnails = info.get('thumbnails', [])
                    if thumbnails:
                        video_info['thumbnails'] = [
                            {
                                'url': t.get('url'),
                                'width': t.get('width'),
                                'height': t.get('height'),
                                'resolution': t.get('resolution'),
                            }
                            for t in thumbnails[:10]
                        ]
                    
                    formats = info.get('formats', [])
                    if formats:
                        format_summary = []
                        for fmt in formats[:20]:
                            if fmt.get('filesize') or fmt.get('filesize_approx'):
                                format_summary.append({
                                    'format_id': fmt.get('format_id'),
                                    'ext': fmt.get('ext'),
                                    'resolution': fmt.get('resolution', 'N/A'),
                                    'filesize': fmt.get('filesize') or fmt.get('filesize_approx'),
                                    'filesize_formatted': youtube_utils.format_file_size(
                                        fmt.get('filesize') or fmt.get('filesize_approx') or 0
                                    ),
                                    'vcodec': fmt.get('vcodec', 'none'),
                                    'acodec': fmt.get('acodec', 'none'),
                                    'format_note': fmt.get('format_note', ''),
                                    'fps': fmt.get('fps'),
                                    'tbr': fmt.get('tbr'),
                                    'protocol': fmt.get('protocol', ''),
                                    'has_audio': fmt.get('acodec') != 'none',
                                    'has_video': fmt.get('vcodec') != 'none',
                                })
                        video_info['formats_detailed'] = format_summary
                        
            except Exception as e:
                logger.warning(f"Could not get detailed info: {e}")
                video_info['detailed_info_error'] = str(e)
        
        await cache.set(cache_key, video_info, size=30720)
        
        return video_info
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Info error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting video info: {str(e)}")

@app.get("/formats")
async def get_available_formats(
    url: str = Query(..., description="YouTube video URL")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    try:
        video_id = youtube_utils.extract_video_id(url)
        
        cache_key = f"formats:{video_id}"
        cached_formats = await cache.get(cache_key)
        if cached_formats:
            logger.info(f"Cache hit for formats: {video_id}")
            cached_formats['cached'] = True
            return cached_formats
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'listformats': True,
            'skip_download': True,
        }
        
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        loop = asyncio.get_event_loop()
        with downloader._executor as executor:
            info = await loop.run_in_executor(
                executor,
                lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=False)
            )
        
        if not info:
            raise HTTPException(status_code=404, detail="Video not found or unavailable")
        
        formats = []
        video_formats = []
        audio_formats = []
        adaptive_formats = []
        
        for fmt in info.get('formats', []):
            format_info = {
                'format_id': fmt.get('format_id'),
                'ext': fmt.get('ext'),
                'resolution': fmt.get('resolution', 'N/A'),
                'width': fmt.get('width'),
                'height': fmt.get('height'),
                'fps': fmt.get('fps'),
                'filesize': fmt.get('filesize') or fmt.get('filesize_approx'),
                'filesize_formatted': youtube_utils.format_file_size(
                    fmt.get('filesize') or fmt.get('filesize_approx') or 0
                ),
                'vcodec': fmt.get('vcodec', 'none'),
                'acodec': fmt.get('acodec', 'none'),
                'format_note': fmt.get('format_note', ''),
                'tbr': fmt.get('tbr'),
                'abr': fmt.get('abr'),
                'asr': fmt.get('asr'),
                'protocol': fmt.get('protocol', ''),
                'container': fmt.get('container', ''),
                'dynamic_range': fmt.get('dynamic_range', 'SDR'),
                'has_audio': fmt.get('acodec') != 'none',
                'has_video': fmt.get('vcodec') != 'none',
                'quality_label': downloader._get_quality_label(fmt.get('height', 0)),
                'language': fmt.get('language'),
                'language_preference': fmt.get('language_preference', 0),
            }
            
            formats.append(format_info)
            
            if format_info['has_video'] and format_info['has_audio']:
                video_formats.append(format_info)
            elif format_info['has_audio'] and not format_info['has_video']:
                audio_formats.append(format_info)
            elif format_info['has_video'] and not format_info['has_audio']:
                adaptive_formats.append(format_info)
        
        video_formats.sort(key=lambda x: (x.get('height', 0) or 0, x.get('tbr', 0) or 0), reverse=True)
        audio_formats.sort(key=lambda x: (x.get('abr', 0) or 0, x.get('asr', 0) or 0), reverse=True)
        adaptive_formats.sort(key=lambda x: (x.get('height', 0) or 0, x.get('tbr', 0) or 0), reverse=True)
        
        response = {
            'video_id': video_id,
            'title': info.get('title', 'Unknown'),
            'total_formats': len(formats),
            'formats': formats[:100],
            'categories': {
                'video_with_audio': video_formats[:20],
                'audio_only': audio_formats[:20],
                'video_only': adaptive_formats[:20],
            },
            'recommended': {
                'best_video': video_formats[0] if video_formats else None,
                'best_audio': audio_formats[0] if audio_formats else None,
                'fastest_stream': min(formats, key=lambda x: x.get('filesize', float('inf'))) if formats else None,
                'smallest_file': min(formats, key=lambda x: x.get('filesize', float('inf'))) if formats else None,
            },
            'timestamp': time.time()
        }
        
        await cache.set(cache_key, response, size=51200)
        
        return response
        
    except Exception as e:
        logger.error(f"Formats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting formats: {str(e)}")

@app.get("/search")
async def search_videos(
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=50, description="Number of results (1-50)"),
    type: str = Query("video", description="Type: video, playlist, channel"),
    sort: str = Query("relevance", description="Sort: relevance, rating, date, views"),
    duration: str = Query(None, description="Duration: short, medium, long"),
    force_refresh: bool = Query(False, description="Bypass cache")
):
    if not q or len(q.strip()) < 2:
        return {
            "success": False,
            "query": q,
            "results": [],
            "error": "Search query must be at least 2 characters long",
            "timestamp": time.time()
        }
    
    try:
        cache_key = f"search:{q}:{limit}:{type}:{sort}:{duration}"
        if not force_refresh:
            cached_results = await cache.get(cache_key)
            if cached_results:
                logger.info(f"Cache hit for search: {q}")
                cached_results['cached'] = True
                return cached_results
        
        search_query = q
        
        filters = []
        if type == "playlist":
            filters.append("playlist")
        elif type == "channel":
            filters.append("channel")
        
        if duration == "short":
            filters.append("short")
        elif duration == "medium":
            filters.append("medium")
        elif duration == "long":
            filters.append("long")
        
        results = await youtube_utils.search_youtube_async(search_query, limit)
        
        if sort == "date":
            results.sort(key=lambda x: x.get('upload_date', ''), reverse=True)
        elif sort == "views":
            def parse_views(view_str):
                if isinstance(view_str, int):
                    return view_str
                if isinstance(view_str, str):
                    view_str = view_str.replace(' views', '').replace(',', '')
                    if 'K' in view_str:
                        return float(view_str.replace('K', '')) * 1000
                    elif 'M' in view_str:
                        return float(view_str.replace('M', '')) * 1000000
                    elif 'B' in view_str:
                        return float(view_str.replace('B', '')) * 1000000000
                    else:
                        try:
                            return float(view_str)
                        except:
                            return 0
                return 0
            
            results.sort(key=lambda x: parse_views(x.get('view_count', 0)), reverse=True)
        
        response = {
            'success': True,
            'query': q,
            'type': type,
            'sort': sort,
            'duration_filter': duration,
            'count': len(results),
            'results': results,
            'timestamp': time.time()
        }
        
        cache_ttl = 300
        await cache.set(cache_key, response, size=len(str(results).encode('utf-8')))
        
        return response
        
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        return {
            "success": False,
            "query": q,
            "results": [],
            "error": str(e),
            "timestamp": time.time()
        }

@app.get("/download/audio")
async def download_audio(
    url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("best", description="Audio quality preference"),
    filename: str = Query(None, description="Custom filename (without extension)")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    try:
        result = await downloader.get_stream_info(url, "audio", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Download error'))
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'audio'))
        ext = result.get('format', {}).get('ext', 'mp3')
        
        if filename:
            filename = youtube_utils.clean_title(filename)
            download_filename = f"{filename}.{ext}"
        else:
            download_filename = f"{title}.{ext}"
        
        content_type = get_content_type(ext, 'audio/mpeg')
        
        logger.info(f"Download audio: {title} | Format: {ext}")
        
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=65536),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{download_filename}"',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Content-Type': content_type,
                'X-Audio-Title': title,
                'X-Video-Id': result.get('video_id', ''),
                'X-Audio-Bitrate': str(result.get('format', {}).get('abr', 128)),
                'X-Audio-Codec': result.get('format', {}).get('acodec', 'unknown'),
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download audio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/download/video")
async def download_video(
    url: str = Query(..., description="YouTube video URL"),
    quality: str = Query("best", description="Video quality: low, medium, high, best, 4k"),
    filename: str = Query(None, description="Custom filename (without extension)")
):
    if not youtube_utils.is_valid_youtube_url(url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")
    
    valid_qualities = ["low", "medium", "high", "best", "4k"]
    if quality not in valid_qualities:
        raise HTTPException(status_code=400, detail=f"Invalid quality. Must be one of: {', '.join(valid_qualities)}")
    
    try:
        result = await downloader.get_stream_info(url, "video", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Download error'))
        
        filesize = result.get('format', {}).get('filesize')
        if filesize and filesize > config.MAX_DOWNLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"Video file too large ({filesize // (1024*1024)}MB). "
                      f"Maximum allowed: {config.MAX_DOWNLOAD_SIZE // (1024*1024)}MB. "
                      f"Try a lower quality."
            )
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'video'))
        ext = result.get('format', {}).get('ext', 'mp4')
        height = result.get('format', {}).get('height', 'N/A')
        
        if filename:
            filename = youtube_utils.clean_title(filename)
            download_filename = f"{filename}_{height}p.{ext}"
        else:
            download_filename = f"{title}_{height}p.{ext}"
        
        content_type = get_content_type(ext, 'video/mp4')
        
        logger.info(f"Download video: {title} | Quality: {height}p")
        
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=131072),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{download_filename}"',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Content-Type': content_type,
                'X-Video-Title': title,
                'X-Video-Id': result.get('video_id', ''),
                'X-Video-Quality': f"{height}p",
                'X-Video-Size': result.get('format', {}).get('filesize_formatted', 'N/A'),
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download video error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/health")
async def health_check():
    try:
        cache_stats = cache.get_stats()
        rate_limiter_stats = rate_limiter.get_stats()
        uptime_seconds = time.time() - _request_stats['start_time']
        uptime_hours = uptime_seconds / 3600
        system_stats = system_monitor.get_system_stats()
        
        health_status = {
            "status": "healthy",
            "service": "YouTube Streaming API",
            "version": "4.0.0",
            "timestamp": time.time(),
            "uptime_seconds": uptime_seconds,
            "uptime_hours": round(uptime_hours, 2),
            "environment": "production" if not config.DEBUG else "development",
            "cache": cache_stats,
            "rate_limiter": rate_limiter_stats,
            "requests": {
                "total": _request_stats['total_requests'],
                "by_endpoint": dict(list(_request_stats['requests_by_endpoint'].items())[:10]),
                "unique_ips": len(_request_stats['requests_by_ip']),
                "top_ips": dict(list(_request_stats['requests_by_ip'].items())[:5]),
            },
            "system": system_stats,
            "config": {
                "max_requests_per_minute": config.MAX_REQUESTS_PER_MINUTE,
                "cache_size": config.MAX_CACHE_SIZE,
                "cache_ttl_hours": config.CACHE_TTL // 3600,
                "max_download_size_mb": config.MAX_DOWNLOAD_SIZE // (1024 * 1024),
                "has_cookies": bool(config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE)),
                "has_proxy": bool(config.PROXY),
            },
            "checks": {
                "cache_operational": cache_stats['size'] >= 0,
                "rate_limiter_operational": True,
                "disk_space_adequate": system_stats.get('disk', {}).get('free_gb', 0) > 1,
                "memory_adequate": system_stats.get('memory', {}).get('rss_mb', 0) < 400,
            }
        }
        
        all_checks_pass = all(health_status['checks'].values())
        health_status['overall_healthy'] = all_checks_pass
        
        if not all_checks_pass:
            health_status['status'] = "degraded"
            health_status['issues'] = [
                key for key, value in health_status['checks'].items() 
                if not value
            ]
        
        return health_status
        
    except Exception as e:
        logger.error(f"Health check error: {e}", exc_info=True)
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": time.time()
        }

@app.get("/stats")
async def api_statistics(
    detailed: bool = Query(False, description="Include detailed statistics"),
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    try:
        basic_stats = {
            "total_requests": _request_stats['total_requests'],
            "uptime_seconds": time.time() - _request_stats['start_time'],
            "cache": {
                "size": len(cache.cache),
                "hits": cache.stats['hits'],
                "misses": cache.stats['misses'],
                "hit_ratio": cache.stats['hits'] / max(1, cache.stats['hits'] + cache.stats['misses']),
            },
            "rate_limiter": {
                "active_ips": len(rate_limiter.requests),
                "blocked_requests": rate_limiter.stats['blocked_requests'],
            },
            "timestamp": time.time()
        }
        
        if not detailed:
            return basic_stats
        
        if auth:
            pass
        
        detailed_stats = {
            **basic_stats,
            "requests_by_endpoint": _request_stats['requests_by_endpoint'],
            "requests_by_ip": dict(list(_request_stats['requests_by_ip'].items())[:20]),
            "cache_detailed": cache.get_stats(),
            "rate_limiter_detailed": rate_limiter.get_stats(),
            "system": system_monitor.get_system_stats(),
            "config": {
                "host": config.HOST,
                "port": config.PORT,
                "debug": config.DEBUG,
                "rate_limit_window": config.RATE_LIMIT_WINDOW,
                "max_requests_per_minute": config.MAX_REQUESTS_PER_MINUTE,
                "cache_ttl": config.CACHE_TTL,
                "max_cache_size": config.MAX_CACHE_SIZE,
                "max_download_size": config.MAX_DOWNLOAD_SIZE,
                "log_level": config.LOG_LEVEL,
                "has_cookies": bool(config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE)),
                "has_proxy": bool(config.PROXY),
                "has_youtube_api_key": bool(config.YOUTUBE_API_KEY),
            },
            "performance": {
                "average_request_time": "N/A",
                "peak_requests_per_minute": "N/A",
                "concurrent_requests": "N/A",
            }
        }
        
        return detailed_stats
        
    except Exception as e:
        logger.error(f"Stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting statistics: {str(e)}")

@app.get("/system")
async def system_info():
    try:
        system_stats = system_monitor.get_system_stats()
        
        import psutil
        process = psutil.Process()
        
        system_stats.update({
            "process": {
                "pid": process.pid,
                "name": process.name(),
                "status": process.status(),
                "create_time": datetime.fromtimestamp(process.create_time()).isoformat(),
                "threads": process.num_threads(),
                "open_files": len(process.open_files()),
                "connections": len(process.connections()),
                "cpu_times": {
                    "user": process.cpu_times().user,
                    "system": process.cpu_times().system,
                    "children_user": process.cpu_times().children_user,
                    "children_system": process.cpu_times().children_system,
                }
            },
            "python": {
                "version": sys.version,
                "implementation": sys.implementation.name,
                "path": sys.path[:5],
                "executable": sys.executable,
            },
            "api": {
                "total_requests": _request_stats['total_requests'],
                "uptime_hours": round((time.time() - _request_stats['start_time']) / 3600, 2),
                "cache_size": len(cache.cache),
                "cache_memory_mb": cache.stats['size_bytes'] / (1024 * 1024),
            }
        })
        
        return system_stats
        
    except Exception as e:
        logger.error(f"System info error: {e}", exc_info=True)
        return {
            "error": str(e),
            "timestamp": time.time()
        }

@app.get("/clear-cache")
async def clear_cache_endpoint(
    auth: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        cache_size = len(cache.cache)
        cache_memory = cache.stats['size_bytes']
        
        await cache.clear()
        
        logger.info(f"Cache cleared: {cache_size} entries, {cache_memory / (1024*1024):.2f}MB")
        
        return {
            "success": True,
            "message": "Cache cleared successfully",
            "cleared_entries": cache_size,
            "cleared_memory_mb": round(cache_memory / (1024 * 1024), 2),
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(f"Clear cache error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error clearing cache: {str(e)}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    client_ip = websocket.client.host if websocket.client else "unknown"
    logger.info(f"WebSocket connected: {client_ip}")
    
    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                if data == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": time.time(),
                        "client_ip": client_ip
                    })
                    
                elif data.startswith("info:"):
                    video_url = data[5:]
                    if youtube_utils.is_valid_youtube_url(video_url):
                        result = await downloader.get_stream_info(video_url, "video", "best")
                        await websocket.send_json({
                            "type": "info",
                            "data": result,
                            "timestamp": time.time()
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Invalid YouTube URL",
                            "timestamp": time.time()
                        })
                        
                elif data.startswith("search:"):
                    query = data[7:]
                    if len(query) >= 2:
                        results = await youtube_utils.search_youtube_async(query, limit=5)
                        await websocket.send_json({
                            "type": "search_results",
                            "query": query,
                            "results": results,
                            "count": len(results),
                            "timestamp": time.time()
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Search query too short",
                            "timestamp": time.time()
                        })
                        
                elif data.startswith("stream:audio:"):
                    video_url = data[13:]
                    if youtube_utils.is_valid_youtube_url(video_url):
                        result = await downloader.get_stream_info(video_url, "audio", "best")
                        await websocket.send_json({
                            "type": "audio_stream",
                            "data": result,
                            "timestamp": time.time()
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Invalid YouTube URL",
                            "timestamp": time.time()
                        })
                        
                elif data.startswith("stream:video:"):
                    parts = data[13:].split(":", 1)
                    if len(parts) == 2:
                        video_url, quality = parts
                        if youtube_utils.is_valid_youtube_url(video_url):
                            result = await downloader.get_stream_info(video_url, "video", quality or "best")
                            await websocket.send_json({
                                "type": "video_stream",
                                "data": result,
                                "timestamp": time.time()
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "message": "Invalid YouTube URL",
                                "timestamp": time.time()
                            })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Invalid format. Use: stream:video:URL:QUALITY",
                            "timestamp": time.time()
                        })
                        
                elif data == "stats":
                    cache_stats = cache.get_stats()
                    rate_stats = rate_limiter.get_stats()
                    
                    await websocket.send_json({
                        "type": "stats",
                        "cache": cache_stats,
                        "rate_limiter": rate_stats,
                        "requests": {
                            "total": _request_stats['total_requests'],
                            "unique_ips": len(_request_stats['requests_by_ip']),
                        },
                        "timestamp": time.time()
                    })
                    
                else:
                    await websocket.send_json({
                        "type": "message",
                        "text": f"Received: {data}",
                        "timestamp": time.time()
                    })
                    
            except Exception as e:
                logger.error(f"WebSocket command error: {e}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"Error processing command: {str(e)}",
                    "timestamp": time.time()
                })
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {client_ip}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    error_response = {
        "error": "HTTP Error",
        "message": exc.detail,
        "status_code": exc.status_code,
        "path": request.url.path,
        "method": request.method,
        "timestamp": time.time()
    }
    
    if request.query_params:
        error_response["query_params"] = dict(request.query_params)
    
    logger.warning(f"HTTP {exc.status_code}: {request.method} {request.url.path} - {exc.detail}")
    
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response,
        headers={
            "X-Error-Type": "HTTPException",
            "X-Error-Message": exc.detail[:100]
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    error_response = {
        "error": "Internal Server Error",
        "message": "An unexpected error occurred. Please try again later.",
        "status_code": 500,
        "path": request.url.path,
        "method": request.method,
        "timestamp": time.time(),
        "error_id": hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    }
    
    if config.DEBUG:
        error_response["debug"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": str(exc.__traceback__) if exc.__traceback__ else None
        }
    
    return JSONResponse(
        status_code=500,
        content=error_response,
        headers={
            "X-Error-Type": "InternalError",
            "X-Error-ID": error_response["error_id"]
        }
    )

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico" if os.path.exists("static/favicon.ico") else None)

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🎬 ADVANCED YOUTUBE STREAMING API v4.0.0")
    print("="*70)
    print(f"📁 Download directory: {config.DOWNLOAD_DIR}")
    print(f"🌐 Server URL: http://{config.HOST}:{config.PORT}")
    print(f"📚 Documentation: http://{config.HOST}:{config.PORT}/docs")
    print(f"📊 Health check: http://{config.HOST}:{config.PORT}/health")
    print("="*70)
    
    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        print("🍪 Cookies file: DETECTED")
    else:
        print("⚠️  Cookies file: NOT DETECTED")
    
    if config.PROXY:
        print(f"🌐 Proxy: {config.PROXY}")
    
    print(f"⚡ Rate limit: {config.MAX_REQUESTS_PER_MINUTE} requests/minute per IP")
    print(f"💾 Cache: {config.MAX_CACHE_SIZE} entries, {config.CACHE_TTL//3600}h TTL")
    print(f"📦 Max download: {config.MAX_DOWNLOAD_SIZE//(1024*1024)}MB")
    print("="*70)
    print("🚀 Starting server...")
    print("="*70 + "\n")
    
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info" if not config.DEBUG else "debug",
        access_log=True,
        timeout_keep_alive=30,
        proxy_headers=True
    )