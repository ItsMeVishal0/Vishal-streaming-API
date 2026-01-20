from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import uuid
import time
import logging
import os
from fastapi import FastAPI
import uvicorn
import random
import re
import aiohttp
import asyncio
from typing import Optional

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token storage
tokens = {}
TOKEN_TTL = 300  # 5 minutes

# Proxy list
PROXIES = [
    "http://fwsnmuzj:sbl92ctzme7e@142.111.48.253:7030",
    "http://fwsnmuzj:sbl92ctzme7e@198.105.121.200:6462",
    "http://fwsnmuzj:sbl92ctzme7e@64.137.96.74:6641",
    "http://fwsnmuzj:sbl92ctzme7e@23.26.71.145:5628",
]

def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from YouTube URL or return as-is"""
    # If it's already a video ID (11 chars)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    
    # Try to extract from YouTube URL patterns
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:v=)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    
    # If no pattern matches, return original (might be invalid)
    return url_or_id

async def get_stream_url(video_id: str, media_type: str = "audio"):
    """Enhanced stream URL extraction with multiple formats for audio or video"""
    
    # Clean video ID
    video_id = extract_video_id(video_id)
    
    if media_type == "audio":
        # Audio formats in priority order
        formats = [
            "140",                    # m4a 128k (best for browsers)
            "bestaudio[ext=m4a]",     # Best m4a
            "bestaudio/best",         # Any best audio
            "bestaudio",              # Any audio
            "worstaudio",             # Fallback
        ]
        format_param = ",".join(formats)
        query = ["-f", format_param]
    elif media_type == "video":
        # Video+audio formats in priority order (browser compatible)
        formats = [
            "18",                     # 360p mp4 (most compatible)
            "22",                     # 720p mp4
            "best[height<=480]",      # Best up to 480p
            "best[height<=720]",      # Best up to 720p
            "best",                   # Any best quality
        ]
        format_param = ",".join(formats)
        query = ["-f", format_param]
    else:
        raise ValueError(f"Invalid media type: {media_type}")
    
    # Shuffle proxies for load balancing
    shuffled_proxies = PROXIES.copy()
    random.shuffle(shuffled_proxies)
    
    # First try without proxy
    try:
        cmd = ["yt-dlp", "--ignore-errors", "--no-warnings"] + query + ["-g", f"https://youtu.be/{video_id}"]
        logger.info(f"Trying command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.stdout and result.stdout.strip().startswith('http'):
            logger.info(f"✓ {media_type.upper()} Success without proxy")
            return result.stdout.strip().split('\n')[0]  # Get first URL
    except Exception as e:
        logger.error(f"Direct {media_type} extraction failed: {e}")
    
    # Then try with proxies
    for proxy in shuffled_proxies:
        try:
            cmd = ["yt-dlp", "--ignore-errors", "--no-warnings", "--proxy", proxy] + query + ["-g", f"https://youtu.be/{video_id}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.stdout and result.stdout.strip().startswith('http'):
                logger.info(f"✓ {media_type.upper()} Success with proxy: {proxy}")
                return result.stdout.strip().split('\n')[0]  # Get first URL
        except Exception as e:
            logger.error(f"Proxy {media_type} extraction failed: {e}")
            continue
    
    return None

def get_formats_list(video_id: str):
    """Get available formats for a video"""
    try:
        cmd = ["yt-dlp", "--list-formats", f"https://youtu.be/{video_id}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout
    except:
        return "Cannot get formats list"

async def proxy_stream(url: str, headers: dict = None):
    """Proxy stream through server to avoid CORS and referrer issues"""
    if not url:
        return None
    
    try:
        async with aiohttp.ClientSession() as session:
            # Add headers to mimic browser request
            request_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Accept-Encoding': 'identity;q=1, *;q=0',
                'Accept-Language': 'en-US,en;q=0.9',
                'Range': 'bytes=0-',
                'Referer': 'https://www.youtube.com/',
                'Origin': 'https://www.youtube.com'
            }
            
            if headers:
                request_headers.update(headers)
            
            async with session.get(url, headers=request_headers, timeout=30) as response:
                if response.status == 200 or response.status == 206:
                    # Get content type
                    content_type = response.headers.get('Content-Type', 'application/octet-stream')
                    
                    # Create streaming response
                    async def stream_generator():
                        async for chunk in response.content.iter_chunked(8192):
                            yield chunk
                    
                    return StreamingResponse(
                        stream_generator(),
                        media_type=content_type,
                        headers={
                            'Content-Type': content_type,
                            'Accept-Ranges': 'bytes',
                            'Content-Disposition': 'inline',
                            'Cache-Control': 'no-cache',
                            'Access-Control-Allow-Origin': '*',
                        }
                    )
    except Exception as e:
        logger.error(f"Proxy stream error: {e}")
    
    return None

@app.get("/")
async def root():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Audio & Video Stream</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; }
            .container { background: #f9f9f9; padding: 30px; border-radius: 10px; }
            input { padding: 10px; width: 300px; margin-right: 10px; margin-bottom: 10px; }
            select { padding: 10px; margin-right: 10px; }
            button { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }
            .audio-btn { background: #ff0000; color: white; }
            .video-btn { background: #4285f4; color: white; }
            .test-buttons { margin: 20px 0; }
            .test-btn { background: #34a853; color: white; }
            .player-container { margin-top: 20px; }
            video, audio { width: 100%; margin-top: 10px; }
            .tabs { display: flex; margin-bottom: 20px; }
            .tab { padding: 10px 20px; background: #ddd; cursor: pointer; margin-right: 5px; }
            .tab.active { background: #4285f4; color: white; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
            .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
            .success { background: #d4edda; color: #155724; }
            .error { background: #f8d7da; color: #721c24; }
            .loading { background: #fff3cd; color: #856404; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎵 YouTube Audio & Video Stream</h1>
            
            <div class="tabs">
                <div class="tab active" onclick="switchTab('stream')">Stream</div>
                <div class="tab" onclick="switchTab('download')">Download</div>
                <div class="tab" onclick="switchTab('info')">Video Info</div>
            </div>
            
            <!-- Stream Tab -->
            <div id="stream-tab" class="tab-content active">
                <p>Enter YouTube Video ID or URL:</p>
                <input type="text" id="videoId" placeholder="dQw4w9WgXcQ or https://youtu.be/dQw4w9WgXcQ" value="dQw4w9WgXcQ" style="width: 400px;">
                <br>
                <select id="mediaType">
                    <option value="audio">Audio Only</option>
                    <option value="video">Video + Audio</option>
                </select>
                <button class="audio-btn" onclick="playStream()">▶ Play Stream</button>
                <button class="video-btn" onclick="previewVideo()">📺 Preview Video</button>
                
                <div class="test-buttons">
                    <p>Test Videos:</p>
                    <button class="test-btn" onclick="testVideo('dQw4w9WgXcQ', 'video')">🎬 Rick Roll (Video)</button>
                    <button class="test-btn" onclick="testVideo('dQw4w9WgXcQ', 'audio')">🎵 Rick Roll (Audio)</button>
                    <button class="test-btn" onclick="testVideo('jNQXAC9IVRw', 'video')">📹 First YouTube Video</button>
                    <button class="test-btn" onclick="testVideo('9bZkp7q19f0', 'video')">🕺 Gangnam Style</button>
                </div>
                
                <div id="player" style="display:none;">
                    <h3>Now Playing:</h3>
                    <div id="audioPlayerContainer" style="display:none;">
                        <audio id="audioPlayer" controls autoplay></audio>
                    </div>
                    <div id="videoPlayerContainer" style="display:none;">
                        <video id="videoPlayer" controls autoplay width="800"></video>
                    </div>
                    <div id="status" class="status"></div>
                </div>
            </div>
            
            <!-- Download Tab -->
            <div id="download-tab" class="tab-content">
                <h3>Download Options</h3>
                <input type="text" id="downloadVideoId" placeholder="dQw4w9WgXcQ" value="dQw4w9WgXcQ" style="width: 400px;">
                <br><br>
                <select id="downloadType">
                    <option value="audio">Audio Only (.mp3)</option>
                    <option value="video">Video + Audio (.mp4)</option>
                </select>
                <button onclick="downloadMedia()">⬇ Download</button>
                <div id="downloadStatus" class="status"></div>
            </div>
            
            <!-- Info Tab -->
            <div id="info-tab" class="tab-content">
                <h3>Video Information</h3>
                <input type="text" id="infoVideoId" placeholder="dQw4w9WgXcQ" value="dQw4w9WgXcQ" style="width: 400px;">
                <button onclick="getVideoInfo()">📊 Get Info</button>
                <div id="infoResult" style="background: #eee; padding: 15px; border-radius: 5px; max-height: 400px; overflow: auto; margin-top: 10px;"></div>
            </div>
            
            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
                <h3>API Endpoints:</h3>
                <ul>
                    <li><code>/proxy/audio/{video_id}</code> - Proxied audio stream (Recommended)</li>
                    <li><code>/proxy/video/{video_id}</code> - Proxied video stream (Recommended)</li>
                    <li><code>/direct/audio/{video_id}</code> - Direct audio stream</li>
                    <li><code>/direct/video/{video_id}</code> - Direct video stream</li>
                    <li><code>/download/audio/{video_id}</code> - Download audio</li>
                    <li><code>/download/video/{video_id}</code> - Download video</li>
                    <li><code>/info/{video_id}</code> - Get video info</li>
                    <li><code>/health</code> - Health check</li>
                </ul>
            </div>
        </div>
        
        <script>
            function switchTab(tabName) {
                // Update tabs
                document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
                
                event.target.classList.add('active');
                document.getElementById(tabName + '-tab').classList.add('active');
            }
            
            async function playStream() {
                const videoInput = document.getElementById('videoId').value;
                const mediaType = document.getElementById('mediaType').value;
                
                if (!videoInput.trim()) {
                    showStatus('Please enter a video ID or URL', 'error');
                    return;
                }
                
                document.getElementById('player').style.display = 'block';
                showStatus('Loading stream...', 'loading');
                
                // Extract video ID
                const videoId = extractVideoId(videoInput);
                
                if (mediaType === 'audio') {
                    document.getElementById('audioPlayerContainer').style.display = 'block';
                    document.getElementById('videoPlayerContainer').style.display = 'none';
                    
                    const audio = document.getElementById('audioPlayer');
                    // Use proxied stream for better compatibility
                    audio.src = `/proxy/audio/${videoId}`;
                    
                    audio.onloadeddata = () => {
                        showStatus(`Playing Audio: ${videoId}`, 'success');
                    };
                    
                    audio.onerror = (e) => {
                        console.error('Audio error:', e);
                        showStatus('Error playing audio. Try downloading or using VLC.', 'error');
                    };
                } else {
                    document.getElementById('videoPlayerContainer').style.display = 'block';
                    document.getElementById('audioPlayerContainer').style.display = 'none';
                    
                    const video = document.getElementById('videoPlayer');
                    // Use proxied stream for better compatibility
                    video.src = `/proxy/video/${videoId}`;
                    
                    video.onloadeddata = () => {
                        showStatus(`Playing Video: ${videoId}`, 'success');
                    };
                    
                    video.onerror = (e) => {
                        console.error('Video error:', e);
                        showStatus('Error playing video. Try audio-only or different video.', 'error');
                    };
                }
            }
            
            function previewVideo() {
                const videoInput = document.getElementById('videoId').value;
                const mediaType = document.getElementById('mediaType').value;
                const videoId = extractVideoId(videoInput);
                
                if (mediaType === 'audio') {
                    window.open(`/proxy/audio/${videoId}`, '_blank');
                } else {
                    window.open(`/proxy/video/${videoId}`, '_blank');
                }
            }
            
            function testVideo(videoId, type) {
                document.getElementById('videoId').value = videoId;
                document.getElementById('mediaType').value = type;
                playStream();
            }
            
            function downloadMedia() {
                const videoInput = document.getElementById('downloadVideoId').value;
                const type = document.getElementById('downloadType').value;
                
                if (!videoInput.trim()) {
                    showDownloadStatus('Please enter a video ID or URL', 'error');
                    return;
                }
                
                const videoId = extractVideoId(videoInput);
                showDownloadStatus('Starting download...', 'loading');
                
                window.open(`/download/${type}/${videoId}`, '_blank');
                showDownloadStatus(`Download started for ${videoId}`, 'success');
            }
            
            async function getVideoInfo() {
                const videoInput = document.getElementById('infoVideoId').value;
                
                if (!videoInput.trim()) {
                    document.getElementById('infoResult').innerHTML = '<div class="error">Please enter a video ID or URL</div>';
                    return;
                }
                
                const videoId = extractVideoId(videoInput);
                document.getElementById('infoResult').innerHTML = 'Loading...';
                
                try {
                    const response = await fetch(`/info/${videoId}`);
                    const data = await response.json();
                    
                    let html = `<h4>Video ID: ${videoId}</h4>`;
                    html += `<p><strong>Audio Available:</strong> ${data.audio_available ? '✅ Yes' : '❌ No'}</p>`;
                    html += `<p><strong>Video Available:</strong> ${data.video_available ? '✅ Yes' : '❌ No'}</p>`;
                    
                    if (data.audio_available) {
                        html += `<p><a href="/proxy/audio/${videoId}" target="_blank">🎵 Stream Audio</a> | <a href="/download/audio/${videoId}" target="_blank">⬇ Download Audio</a></p>`;
                    }
                    
                    if (data.video_available) {
                        html += `<p><a href="/proxy/video/${videoId}" target="_blank">🎬 Stream Video</a> | <a href="/download/video/${videoId}" target="_blank">⬇ Download Video</a></p>`;
                    }
                    
                    html += `<pre style="max-height: 300px; overflow: auto;">${data.formats_info || 'No format info available'}</pre>`;
                    document.getElementById('infoResult').innerHTML = html;
                } catch (error) {
                    document.getElementById('infoResult').innerHTML = `<div class="error">Error: ${error}</div>`;
                }
            }
            
            function extractVideoId(input) {
                // Simple extraction
                let videoId = input.trim();
                
                // Extract from URL
                const patterns = [
                    /(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
                    /(?:v=)([a-zA-Z0-9_-]{11})/
                ];
                
                for (const pattern of patterns) {
                    const match = videoId.match(pattern);
                    if (match) {
                        return match[1];
                    }
                }
                
                return videoId;
            }
            
            function showStatus(message, type) {
                const statusDiv = document.getElementById('status');
                statusDiv.textContent = message;
                statusDiv.className = 'status ' + type;
            }
            
            function showDownloadStatus(message, type) {
                const statusDiv = document.getElementById('downloadStatus');
                statusDiv.textContent = message;
                statusDiv.className = 'status ' + type;
            }
            
            // Enter key support
            document.getElementById('videoId').addEventListener('keypress', function(e) {
                if (e.key === 'Enter') {
                    playStream();
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/proxy/{media_type}/{video_id}")
async def proxy_stream_endpoint(media_type: str, video_id: str, request: Request):
    """Proxy stream through server"""
    logger.info(f"Proxy {media_type} stream request: {video_id}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Get stream URL
    url = await get_stream_url(video_id, media_type)
    
    if not url:
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Could not get {media_type} stream URL",
                "video_id": video_id,
                "suggestions": [
                    "Try updating yt-dlp: pip install --upgrade yt-dlp",
                    "Try a different video",
                    "The video might be restricted or unavailable"
                ]
            }
        )
    
    logger.info(f"Proxying {media_type} URL: {url[:100]}...")
    
    # Forward range header if present
    headers = {}
    if 'range' in request.headers:
        headers['Range'] = request.headers['range']
    
    # Proxy the stream
    return await proxy_stream(url, headers)

@app.get("/direct/{media_type}/{video_id}")
async def stream_direct(media_type: str, video_id: str):
    """Direct stream without token (for download or external players)"""
    logger.info(f"Direct {media_type} stream request: {video_id}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Get stream URL
    url = await get_stream_url(video_id, media_type)
    
    if url:
        # Add headers for direct streaming
        headers = {
            'Referer': 'https://www.youtube.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        if media_type == "audio":
            headers['Content-Type'] = 'audio/mp4'
        else:
            headers['Content-Type'] = 'video/mp4'
        
        return RedirectResponse(url, headers=headers)
    else:
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Could not get {media_type} stream URL",
                "video_id": video_id,
                "suggestions": [
                    "Try using /proxy endpoint for browser streaming",
                    "Try updating yt-dlp",
                    "Try a different video"
                ]
            }
        )

@app.get("/download/{media_type}/{video_id}")
async def download_media(media_type: str, video_id: str):
    """Download audio or video"""
    logger.info(f"Download {media_type} request: {video_id}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Clean video ID
    clean_video_id = extract_video_id(video_id)
    
    url = await get_stream_url(clean_video_id, media_type)
    
    if url:
        if media_type == "audio":
            filename = f"{clean_video_id}.mp3"
            content_type = "audio/mpeg"
        else:
            filename = f"{clean_video_id}.mp4"
            content_type = "video/mp4"
        
        # Redirect with download headers
        return RedirectResponse(
            url,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": content_type,
                "Referer": "https://www.youtube.com/"
            }
        )
    else:
        raise HTTPException(500, f"Cannot download {media_type}")

@app.get("/info/{video_id}")
async def get_video_info(video_id: str):
    """Get video information and available formats"""
    logger.info(f"Info request: {video_id}")
    
    # Clean video ID
    clean_video_id = extract_video_id(video_id)
    
    formats_info = get_formats_list(clean_video_id)
    
    # Test audio and video streams
    audio_url = await get_stream_url(clean_video_id, "audio")
    video_url = await get_stream_url(clean_video_id, "video")
    
    return {
        "video_id": clean_video_id,
        "original_input": video_id,
        "timestamp": time.time(),
        "audio_available": bool(audio_url),
        "video_available": bool(video_url),
        "formats_info": formats_info[:5000] if formats_info else "No info available",
        "stream_urls": {
            "proxy_audio": f"/proxy/audio/{clean_video_id}" if audio_url else None,
            "proxy_video": f"/proxy/video/{clean_video_id}" if video_url else None,
            "direct_audio": f"/direct/audio/{clean_video_id}" if audio_url else None,
            "direct_video": f"/direct/video/{clean_video_id}" if video_url else None
        },
        "download_links": {
            "audio": f"/download/audio/{clean_video_id}" if audio_url else None,
            "video": f"/download/video/{clean_video_id}" if video_url else None
        }
    }

@app.get("/test/{video_id}")
async def test_ytdlp(video_id: str):
    """Test yt-dlp directly"""
    # Clean video ID
    clean_video_id = extract_video_id(video_id)
    
    logger.info(f"Testing video: {clean_video_id}")
    
    results = []
    
    # Test common formats
    test_formats = [
        ("audio", "140", "m4a 128k"),
        ("audio", "bestaudio[ext=m4a]", "Best m4a"),
        ("video", "18", "360p mp4"),
        ("video", "22", "720p mp4"),
        ("video", "best[height<=480]", "Best up to 480p"),
    ]
    
    for media_type, fmt, description in test_formats:
        try:
            cmd = ["timeout", "20", "yt-dlp", "--ignore-errors", "-f", fmt, "-g", f"https://youtu.be/{clean_video_id}"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            success = result.returncode == 0 and result.stdout and 'http' in result.stdout
            
            results.append({
                "type": media_type,
                "format": fmt,
                "description": description,
                "success": success,
                "returncode": result.returncode,
                "output": result.stdout[:200] if result.stdout else "",
                "error": result.stderr[:100] if result.stderr else ""
            })
        except Exception as e:
            results.append({
                "type": media_type,
                "format": fmt,
                "description": description,
                "error": str(e)
            })
    
    return {
        "video_id": clean_video_id,
        "original_input": video_id,
        "timestamp": time.time(),
        "tests": results,
        "recommendations": {
            "audio": "Use format 140 (m4a 128k) for browser compatibility",
            "video": "Use format 18 (360p) or 22 (720p) for best compatibility"
        }
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    # Test a known working video
    test_video = "dQw4w9WgXcQ"
    
    try:
        audio_url = await get_stream_url(test_video, "audio")
        video_url = await get_stream_url(test_video, "video")
        
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "active_tokens": len(tokens),
            "audio_working": bool(audio_url),
            "video_working": bool(video_url),
            "proxies_available": len(PROXIES),
            "test_video": test_video,
            "message": "API is operational. Use /proxy endpoints for browser streaming."
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "message": "Some features may not work properly"
        }

@app.get("/help")
async def help_endpoint():
    """Help documentation"""
    return {
        "api_endpoints": {
            "proxied_streams": {
                "audio": "GET /proxy/audio/{video_id}",
                "video": "GET /proxy/video/{video_id}",
                "description": "Recommended for browser playback"
            },
            "direct_streams": {
                "audio": "GET /direct/audio/{video_id}",
                "video": "GET /direct/video/{video_id}",
                "description": "For external players/downloaders"
            },
            "download": {
                "audio": "GET /download/audio/{video_id}",
                "video": "GET /download/video/{video_id}"
            },
            "info": "GET /info/{video_id}",
            "test": "GET /test/{video_id}",
            "health": "GET /health"
        },
        "examples": {
            "proxied_audio": "http://13.62.231.130:8080/proxy/audio/dQw4w9WgXcQ",
            "proxied_video": "http://13.62.231.130:8080/proxy/video/dQw4w9WgXcQ",
            "download_audio": "http://13.62.231.130:8080/download/audio/dQw4w9WgXcQ",
            "get_info": "http://13.62.231.130:8080/info/dQw4w9WgXcQ"
        },
        "usage_tips": [
            "Use /proxy endpoints for browser playback",
            "Use /direct endpoints for VLC or other media players",
            "If streaming fails, try a different video",
            "Some videos may be restricted or unavailable"
        ]
    }



# ... existing code ...

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level="info"
    )
