import os
import sys
import time
import logging
import asyncio
from typing import Optional, Dict
from contextlib import asynccontextmanager

import yt_dlp
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    StreamingResponse, 
    RedirectResponse, 
    JSONResponse, 
    HTMLResponse,
    FileResponse
)
from fastapi.staticfiles import StaticFiles
import aiohttp
from aiohttp import ClientTimeout

sys.path.append('.')

from config import config
from utils import youtube_utils, rate_limiter, cache, downloader

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Global request tracking
_request_stats = {
    'total_requests': 0,
    'start_time': time.time(),
}

# Lifespan events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown"""
    logger.info("🚀 YouTube Streaming API starting...")
    logger.info(f"📁 Download directory: {config.DOWNLOAD_DIR}")
    logger.info(f"🌐 Server: http://{config.HOST}:{config.PORT}")
    
    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        logger.info("🍪 Cookies file detected")
    else:
        logger.warning("⚠️  No cookies.txt file found")
    
    # Create aiohttp session
    app.state.http_session = aiohttp.ClientSession(
        timeout=ClientTimeout(total=60),
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    
    yield
    
    # Shutdown
    logger.info("👋 Shutting down...")
    
    if hasattr(app.state, 'http_session'):
        await app.state.http_session.close()

app = FastAPI(
    title="YouTube Streaming API",
    version="1.0.0",
    description="Simple YouTube streaming and download API",
    lifespan=lifespan
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware"""
    client_ip = request.client.host if request.client else "unknown"
    
    _request_stats['total_requests'] += 1
    
    # Check rate limit
    if not await rate_limiter.check_limit(client_ip):
        logger.warning(f"Rate limit exceeded for {client_ip}")
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "message": f"Maximum {config.MAX_REQUESTS_PER_MINUTE} requests per minute",
                "retry_after": 60,
            },
            headers={"Retry-After": "60"}
        )
    
    try:
        response = await call_next(request)
        return response
    except Exception as e:
        logger.error(f"Request error: {e}")
        raise

app.mount("/static", StaticFiles(directory="static"), name="static")

# Helper functions
def get_content_type(ext: str) -> str:
    """Get content type based on extension"""
    content_types = {
        'mp3': 'audio/mpeg',
        'm4a': 'audio/mp4',
        'webm': 'audio/webm',
        'mp4': 'video/mp4',
        'webm': 'video/webm',
    }
    
    ext = ext.lower().lstrip('.')
    return content_types.get(ext, 'application/octet-stream')

async def stream_file_generator(url: str, chunk_size: int = 8192):
    """Generator for streaming files from URL"""
    try:
        async with app.state.http_session.get(url) as response:
            response.raise_for_status()
            
            async for chunk in response.content.iter_chunked(chunk_size):
                yield chunk
                
    except aiohttp.ClientError as e:
        logger.error(f"Error streaming from {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Stream error: {str(e)}")

# ==================== MAIN ENDPOINTS ====================

@app.get("/")
async def root():
    """Root endpoint"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Streaming API</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .container { max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #333; }
            .endpoint { background: #f8f9fa; padding: 15px; margin: 15px 0; border-radius: 5px; border-left: 4px solid #007bff; }
            .method { background: #007bff; color: white; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; margin-right: 10px; }
            .url { font-family: monospace; background: #e9ecef; padding: 8px; border-radius: 3px; margin: 5px 0; }
            .test { margin-top: 20px; }
            input { padding: 10px; width: 70%; margin-right: 10px; }
            button { padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 3px; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>YouTube Streaming API</h1>
            <p>Simple API for streaming and downloading YouTube videos</p>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/download?url=VIDEO_URL&type=audio</div>
                <p>Download audio from YouTube video</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/stream/VIDEO_ID?type=audio</div>
                <p>Stream audio (redirects to direct URL)</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/info?url=VIDEO_URL</div>
                <p>Get video information</p>
            </div>
            
            <div class="endpoint">
                <span class="method">GET</span>
                <div class="url">/health</div>
                <p>Health check endpoint</p>
            </div>
            
            <div class="test">
                <h3>Quick Test</h3>
                <input type="text" id="testUrl" placeholder="Enter YouTube URL or ID" value="dQw4w9WgXcQ">
                <button onclick="testAudio()">Test Audio</button>
                <button onclick="testVideo()">Test Video</button>
                <div id="result" style="margin-top: 10px;"></div>
            </div>
            
            <p style="margin-top: 30px; color: #666;">
                Total Requests: <span id="totalRequests">0</span> | 
                Uptime: <span id="uptime">0</span> seconds
            </p>
        </div>
        
        <script>
            async function testAudio() {
                const url = document.getElementById('testUrl').value;
                const resultDiv = document.getElementById('result');
                resultDiv.innerHTML = '<p>Testing audio download...</p>';
                
                try {
                    const response = await fetch(`/download?url=${encodeURIComponent(url)}&type=audio`);
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'audio.mp3';
                        document.body.appendChild(a);
                        a.click();
                        resultDiv.innerHTML = '<p style="color: green;">✅ Audio download started!</p>';
                    } else {
                        const error = await response.json();
                        resultDiv.innerHTML = `<p style="color: red;">❌ Error: ${error.message}</p>`;
                    }
                } catch (error) {
                    resultDiv.innerHTML = `<p style="color: red;">❌ Error: ${error.message}</p>`;
                }
            }
            
            async function testVideo() {
                const url = document.getElementById('testUrl').value;
                const resultDiv = document.getElementById('result');
                resultDiv.innerHTML = '<p>Testing video stream...</p>';
                
                try {
                    const videoId = url.includes('youtu.be/') ? url.split('youtu.be/')[1] : 
                                  url.includes('v=') ? url.split('v=')[1] : url;
                    
                    window.open(`/stream/${videoId}?type=video`, '_blank');
                    resultDiv.innerHTML = '<p style="color: green;">✅ Video stream opened in new tab!</p>';
                } catch (error) {
                    resultDiv.innerHTML = `<p style="color: red;">❌ Error: ${error.message}</p>`;
                }
            }
            
            async function updateStats() {
                try {
                    const response = await fetch('/stats');
                    if (response.ok) {
                        const data = await response.json();
                        document.getElementById('totalRequests').textContent = data.total_requests;
                        document.getElementById('uptime').textContent = Math.floor(data.uptime_seconds);
                    }
                } catch (error) {
                    console.error('Failed to update stats:', error);
                }
            }
            
            // Update stats every 10 seconds
            updateStats();
            setInterval(updateStats, 10000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "YouTube Streaming API",
        "timestamp": time.time(),
        "uptime_seconds": time.time() - _request_stats['start_time'],
        "total_requests": _request_stats['total_requests'],
        "cache_size": len(cache.cache),
        "rate_limit": config.MAX_REQUESTS_PER_MINUTE,
    }

@app.get("/stats")
async def get_stats():
    """Get API statistics"""
    return {
        "total_requests": _request_stats['total_requests'],
        "uptime_seconds": time.time() - _request_stats['start_time'],
        "cache_size": len(cache.cache),
        "rate_limit": config.MAX_REQUESTS_PER_MINUTE,
        "timestamp": time.time(),
    }

@app.get("/info")
async def get_video_info(
    url: str = Query(..., description="YouTube video URL or ID")
):
    """Get video information"""
    try:
        result = await downloader.get_stream_info(url, "video", "best")
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Info extraction failed'))
        
        return {
            'video_id': result.get('video_id'),
            'title': result.get('title'),
            'duration': result.get('duration'),
            'duration_formatted': result.get('duration_formatted'),
            'thumbnail': result.get('thumbnail'),
            'channel': result.get('channel'),
            'view_count': result.get('view_count'),
            'type': result.get('type'),
            'timestamp': time.time()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Info error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting video info: {str(e)}")

# ==================== COMPATIBILITY ENDPOINTS ====================
# These match your client's expected endpoints

@app.get("/download")
async def download_endpoint(
    url: str = Query(..., description="YouTube URL or video ID"),
    type: str = Query("audio", description="Type: audio or video"),
    quality: str = Query("best", description="Quality: low, medium, high, best")
):
    """
    Download endpoint - matches your client call:
    GET /download?url=VIDEO_ID&type=audio
    """
    logger.info(f"Download request: {url} | Type: {type} | Quality: {quality}")
    
    try:
        # Get stream info
        result = await downloader.get_stream_info(url, type, quality)
        
        if result['status'] != 'success':
            error_msg = result.get('message', 'Download failed')
            logger.error(f"Download failed: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'download'))
        video_id = result.get('video_id', 'unknown')
        
        # Determine filename and content type
        if type == "audio":
            filename = f"{title}.mp3"
            content_type = "audio/mpeg"
        else:
            height = result.get('format', {}).get('height', '')
            if height:
                filename = f"{title}_{height}p.mp4"
            else:
                filename = f"{title}.mp4"
            content_type = "video/mp4"
        
        logger.info(f"Downloading: {filename} from {stream_url[:100]}...")
        
        # Return as downloadable file
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=65536),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Content-Type': content_type,
                'X-Video-Title': title,
                'X-Video-Id': video_id,
                'X-Endpoint-Type': 'compat-download'
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/stream/{video_id}")
async def stream_endpoint(
    video_id: str,
    type: str = Query("audio", description="Type: audio or video"),
    quality: str = Query("best", description="Quality: low, medium, high, best"),
    token: Optional[str] = Query(None, description="Optional token (ignored)")
):
    """
    Stream endpoint - matches your client call:
    GET /stream/VIDEO_ID?type=audio&token=TOKEN
    """
    logger.info(f"Stream request: {video_id} | Type: {type} | Quality: {quality}")
    
    try:
        # Build URL from video ID
        url = f"https://youtu.be/{video_id}"
        
        # Get stream info
        result = await downloader.get_stream_info(url, type, quality)
        
        if result['status'] != 'success':
            error_msg = result.get('message', 'Stream failed')
            logger.error(f"Stream failed: {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'stream'))
        
        # Determine content type
        if type == "audio":
            content_type = "audio/mpeg"
        else:
            content_type = "video/mp4"
        
        logger.info(f"Streaming: {title} to {stream_url[:100]}...")
        
        # Return redirect to stream URL
        response = RedirectResponse(url=stream_url, status_code=302)
        
        response.headers.update({
            'Accept-Ranges': 'bytes',
            'Content-Type': content_type,
            'Cache-Control': 'public, max-age=7200',
            'Access-Control-Allow-Origin': '*',
            'X-Video-Title': title,
            'X-Video-Id': video_id,
            'X-Endpoint-Type': 'compat-stream'
        })
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stream endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Stream error: {str(e)}")

# ==================== ENHANCED ENDPOINTS ====================

@app.get("/stream/audio")
async def stream_audio_direct(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("best", description="Audio quality")
):
    """Direct audio streaming endpoint"""
    try:
        result = await downloader.get_stream_info(url, "audio", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Audio stream failed'))
        
        # Return redirect to audio stream
        response = RedirectResponse(url=result['stream_url'], status_code=302)
        response.headers.update({
            'Accept-Ranges': 'bytes',
            'Content-Type': 'audio/mpeg',
            'Cache-Control': 'public, max-age=86400',
            'X-Video-Title': youtube_utils.clean_title(result.get('title', 'audio')),
            'X-Video-Id': result.get('video_id'),
        })
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio stream error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Audio streaming error: {str(e)}")

@app.get("/stream/video")
async def stream_video_direct(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("best", description="Video quality: low, medium, high, best")
):
    """Direct video streaming endpoint"""
    try:
        result = await downloader.get_stream_info(url, "video", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Video stream failed'))
        
        # Return redirect to video stream
        response = RedirectResponse(url=result['stream_url'], status_code=302)
        response.headers.update({
            'Accept-Ranges': 'bytes',
            'Content-Type': 'video/mp4',
            'Cache-Control': 'public, max-age=7200',
            'X-Video-Title': youtube_utils.clean_title(result.get('title', 'video')),
            'X-Video-Id': result.get('video_id'),
            'X-Video-Quality': str(result.get('format', {}).get('height', 'N/A')),
        })
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video stream error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Video streaming error: {str(e)}")

@app.get("/download/audio")
async def download_audio_direct(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("best", description="Audio quality")
):
    """Direct audio download endpoint"""
    try:
        result = await downloader.get_stream_info(url, "audio", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Audio download failed'))
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'audio'))
        filename = f"{title}.mp3"
        
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=65536),
            media_type='audio/mpeg',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Content-Type': 'audio/mpeg',
                'X-Audio-Title': title,
                'X-Video-Id': result.get('video_id'),
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Audio download error: {str(e)}")

@app.get("/download/video")
async def download_video_direct(
    url: str = Query(..., description="YouTube URL"),
    quality: str = Query("best", description="Video quality: low, medium, high, best")
):
    """Direct video download endpoint"""
    try:
        result = await downloader.get_stream_info(url, "video", quality)
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Video download failed'))
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'video'))
        height = result.get('format', {}).get('height', '')
        filename = f"{title}_{height}p.mp4" if height else f"{title}.mp4"
        
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=131072),
            media_type='video/mp4',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'no-cache',
                'Content-Type': 'video/mp4',
                'X-Video-Title': title,
                'X-Video-Id': result.get('video_id'),
                'X-Video-Quality': str(height),
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Video download error: {str(e)}")

@app.get("/simple")
async def simple_download(
    url: str = Query(..., description="YouTube URL or ID"),
    format: str = Query("mp3", description="Format: mp3, mp4")
):
    """Simple download endpoint for basic clients"""
    try:
        type = "audio" if format == "mp3" else "video"
        result = await downloader.get_stream_info(url, type, "best")
        
        if result['status'] != 'success':
            raise HTTPException(status_code=500, detail=result.get('message', 'Download failed'))
        
        stream_url = result['stream_url']
        title = youtube_utils.clean_title(result.get('title', 'download'))
        
        if format == "mp3":
            filename = f"{title}.mp3"
            content_type = "audio/mpeg"
        else:
            filename = f"{title}.mp4"
            content_type = "video/mp4"
        
        return StreamingResponse(
            stream_file_generator(stream_url, chunk_size=65536),
            media_type=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'no-cache',
                'Content-Type': content_type,
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Simple download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/direct")
async def direct_stream(
    url: str = Query(..., description="YouTube URL or ID"),
    type: str = Query("audio", description="Type: audio or video")
):
    """Direct stream endpoint - returns JSON with stream URL"""
    try:
        result = await downloader.get_stream_info(url, type, "best")
        
        if result['status'] != 'success':
            return {
                "success": False,
                "error": result.get('message', 'Extraction failed'),
                "video_id": result.get('video_id', 'unknown')
            }
        
        return {
            "success": True,
            "stream_url": result.get('stream_url'),
            "video_id": result.get('video_id'),
            "title": result.get('title'),
            "duration": result.get('duration'),
            "type": type,
            "direct": True
        }
        
    except Exception as e:
        logger.error(f"Direct stream error: {e}")
        return {
            "success": False,
            "error": str(e),
            "video_id": youtube_utils.extract_video_id(url) or 'unknown'
        }

@app.get("/clear-cache")
async def clear_cache():
    """Clear all cache (admin endpoint)"""
    await cache.clear()
    return {"success": True, "message": "Cache cleared", "timestamp": time.time()}

@app.get("/test/{video_id}")
async def test_video(video_id: str):
    """Test endpoint for debugging"""
    url = f"https://youtu.be/{video_id}"
    
    try:
        # Test yt-dlp extraction
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if info:
                return {
                    "success": True,
                    "video_id": video_id,
                    "title": info.get('title'),
                    "available": True,
                    "formats_count": len(info.get('formats', [])),
                    "has_audio_formats": any(f.get('acodec') != 'none' for f in info.get('formats', [])),
                    "has_video_formats": any(f.get('vcodec') != 'none' for f in info.get('formats', [])),
                }
            else:
                return {"success": False, "error": "No info extracted"}
                
    except Exception as e:
        return {"success": False, "error": str(e)}

# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    error_response = {
        "error": "HTTP Error",
        "message": exc.detail,
        "status_code": exc.status_code,
        "path": request.url.path,
        "timestamp": time.time()
    }
    
    logger.warning(f"HTTP {exc.status_code}: {request.method} {request.url.path} - {exc.detail}")
    
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    error_response = {
        "error": "Internal Server Error",
        "message": "An unexpected error occurred",
        "status_code": 500,
        "path": request.url.path,
        "timestamp": time.time(),
    }
    
    if config.DEBUG:
        error_response["debug"] = str(exc)
    
    return JSONResponse(
        status_code=500,
        content=error_response
    )

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico" if os.path.exists("static/favicon.ico") else None)

if __name__ == "__main__":
    print("\n" + "="*60)
    print("🎬 YOUTUBE STREAMING API")
    print("="*60)
    print(f"🌐 Server: http://{config.HOST}:{config.PORT}")
    print(f"📚 Endpoints:")
    print(f"   GET /download?url=VIDEO_ID&type=audio")
    print(f"   GET /stream/VIDEO_ID?type=video")
    print(f"   GET /health")
    print(f"   GET /info?url=VIDEO_URL")
    print("="*60)
    
    if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
        print("🍪 Cookies file: DETECTED")
    else:
        print("⚠️  Cookies file: NOT DETECTED")
    
    print(f"⚡ Rate limit: {config.MAX_REQUESTS_PER_MINUTE}/minute per IP")
    print("="*60)
    print("🚀 Starting server...\n")
    
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info" if not config.DEBUG else "debug",
        access_log=True,
    )