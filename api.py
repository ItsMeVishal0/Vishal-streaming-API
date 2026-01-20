from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import subprocess
import uuid
import time
import logging
import random
import re
import aiohttp
import asyncio
from typing import Optional
import os
import json
from pathlib import Path
import psutil
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="YouTube Stream API", version="2.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add GZip middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Token storage
tokens = {}
TOKEN_TTL = 300  # 5 minutes

# Get environment variables
def get_proxies():
    """Load proxies from environment variable"""
    proxies_json = os.getenv("PROXY_LIST", "[]")
    try:
        proxies = json.loads(proxies_json)
        if proxies:
            logger.info(f"Loaded {len(proxies)} proxies from environment")
            return proxies
    except:
        pass
    
    # Fallback to default proxies if none in env
    return [
        "http://fwsnmuzj:sbl92ctzme7e@142.111.48.253:7030",
        "http://fwsnmuzj:sbl92ctzme7e@198.105.121.200:6462",
        "http://fwsnmuzj:sbl92ctzme7e@64.137.96.74:6641",
        "http://fwsnmuzj:sbl92ctzme7e@23.26.71.145:5628",
    ]

# Initialize proxies
PROXIES = get_proxies()

# Cache for stream URLs
stream_cache = {}
CACHE_TTL = 300  # 5 minutes

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

async def get_stream_url(video_id: str, media_type: str = "audio", quality: str = None):
    """Enhanced stream URL extraction with multiple formats for audio or video"""
    
    # Check cache first
    cache_key = f"{video_id}:{media_type}:{quality}"
    if cache_key in stream_cache:
        cached_data = stream_cache[cache_key]
        if time.time() - cached_data['timestamp'] < CACHE_TTL:
            logger.info(f"Cache hit for {cache_key}")
            return cached_data['url']
    
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
        # Video+audio formats based on quality
        if quality == "low":
            formats = ["18", "best[height<=360]", "best"]  # 360p or lower
        elif quality == "medium":
            formats = ["22", "best[height<=720]", "best"]  # 720p
        elif quality == "high":
            formats = ["37", "best[height<=1080]", "best"]  # 1080p
        elif quality == "best":
            formats = ["best", "bestvideo+bestaudio"]  # Best available
        else:
            formats = ["18", "22", "best[height<=720]", "best"]  # Default
        
        format_param = ",".join(formats)
        query = ["-f", format_param]
    else:
        raise ValueError(f"Invalid media type: {media_type}")
    
    # Try without proxy first
    try:
        cmd = ["yt-dlp", "--ignore-errors", "--no-warnings"] + query + ["-g", f"https://youtu.be/{video_id}"]
        logger.info(f"Trying command: {' '.join(cmd[:5])}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.stdout and result.stdout.strip().startswith('http'):
            logger.info(f"✓ {media_type.upper()} Success without proxy")
            url = result.stdout.strip().split('\n')[0]
            # Cache the result
            stream_cache[cache_key] = {
                'url': url,
                'timestamp': time.time()
            }
            return url
    except Exception as e:
        logger.error(f"Direct {media_type} extraction failed: {e}")
    
    # Then try with proxies if available
    if PROXIES:
        shuffled_proxies = PROXIES.copy()
        random.shuffle(shuffled_proxies)
        
        for proxy in shuffled_proxies:
            try:
                cmd = ["yt-dlp", "--ignore-errors", "--no-warnings", "--proxy", proxy] + query + ["-g", f"https://youtu.be/{video_id}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                if result.stdout and result.stdout.strip().startswith('http'):
                    logger.info(f"✓ {media_type.upper()} Success with proxy: {proxy[:20]}...")
                    url = result.stdout.strip().split('\n')[0]
                    # Cache the result
                    stream_cache[cache_key] = {
                        'url': url,
                        'timestamp': time.time()
                    }
                    return url
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
        <title>YouTube Audio & Video Stream - Render</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: Arial, sans-serif; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container { 
                background: white; 
                max-width: 1200px; 
                margin: 0 auto; 
                padding: 30px; 
                border-radius: 20px; 
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            h1 { 
                color: #333; 
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .tag {
                display: inline-block;
                background: #667eea;
                color: white;
                padding: 5px 15px;
                border-radius: 20px;
                font-size: 0.9rem;
                margin-bottom: 20px;
            }
            input, select { 
                padding: 12px; 
                font-size: 16px;
                border: 2px solid #ddd;
                border-radius: 8px;
                margin: 5px;
            }
            input:focus, select:focus {
                border-color: #667eea;
                outline: none;
            }
            button { 
                padding: 12px 24px; 
                margin: 5px; 
                border: none; 
                border-radius: 8px; 
                cursor: pointer; 
                font-size: 16px;
                font-weight: bold;
                transition: all 0.3s;
            }
            .audio-btn { 
                background: #ff0000; 
                color: white; 
            }
            .audio-btn:hover { background: #cc0000; }
            .video-btn { 
                background: #4285f4; 
                color: white; 
            }
            .video-btn:hover { background: #3367d6; }
            .test-buttons { 
                margin: 30px 0; 
                padding: 20px;
                background: #f8f9fa;
                border-radius: 10px;
            }
            .test-btn { 
                background: #34a853; 
                color: white;
                margin: 5px;
            }
            .test-btn:hover { background: #2d9148; }
            .player-container { 
                margin-top: 30px; 
                padding: 20px;
                background: #f8f9fa;
                border-radius: 10px;
            }
            video, audio { 
                width: 100%; 
                margin-top: 20px;
                border-radius: 10px;
            }
            .tabs { 
                display: flex; 
                margin-bottom: 20px;
                background: #f8f9fa;
                border-radius: 10px;
                padding: 5px;
            }
            .tab { 
                padding: 12px 24px; 
                background: transparent; 
                cursor: pointer; 
                margin-right: 5px; 
                border-radius: 8px;
                flex: 1;
                text-align: center;
                transition: all 0.3s;
            }
            .tab.active { 
                background: #4285f4; 
                color: white; 
            }
            .tab-content { 
                display: none; 
                animation: fadeIn 0.5s;
            }
            .tab-content.active { 
                display: block; 
            }
            .status { 
                padding: 15px; 
                margin: 15px 0; 
                border-radius: 8px;
                font-weight: bold;
            }
            .success { 
                background: #d4edda; 
                color: #155724; 
                border-left: 4px solid #28a745;
            }
            .error { 
                background: #f8d7da; 
                color: #721c24; 
                border-left: 4px solid #dc3545;
            }
            .loading { 
                background: #fff3cd; 
                color: #856404; 
                border-left: 4px solid #ffc107;
            }
            .api-endpoints {
                margin-top: 40px;
                padding-top: 20px;
                border-top: 2px solid #eee;
            }
            .api-endpoints h3 {
                margin-bottom: 15px;
                color: #333;
            }
            .endpoint-list {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 10px;
            }
            .endpoint {
                background: #f8f9fa;
                padding: 15px;
                border-radius: 8px;
                border-left: 4px solid #4285f4;
            }
            .method {
                display: inline-block;
                padding: 3px 8px;
                background: #4285f4;
                color: white;
                border-radius: 4px;
                font-size: 0.8rem;
                font-weight: bold;
                margin-right: 10px;
            }
            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }
            @media (max-width: 768px) {
                .container { padding: 15px; }
                input, select { width: 100%; margin: 5px 0; }
                button { width: 100%; margin: 5px 0; }
                .tabs { flex-direction: column; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 YouTube Audio & Video Stream</h1>
            <div class="tag">Deployed on Render • v2.0.0</div>
            <p>High-performance streaming and downloading API for YouTube content</p>
            
            <div class="tabs">
                <div class="tab active" onclick="switchTab('stream')">🎵 Stream</div>
                <div class="tab" onclick="switchTab('download')">📥 Download</div>
                <div class="tab" onclick="switchTab('info')">📊 Info</div>
                <div class="tab" onclick="switchTab('api')">🔧 API</div>
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
                <select id="quality" style="display:none;">
                    <option value="">Auto Quality</option>
                    <option value="low">Low (360p)</option>
                    <option value="medium">Medium (720p)</option>
                    <option value="high">High (1080p)</option>
                    <option value="best">Best Available</option>
                </select>
                <button class="audio-btn" onclick="playStream()">▶ Play Stream</button>
                <button class="video-btn" onclick="previewVideo()">📺 Preview Video</button>
                
                <div class="test-buttons">
                    <p><strong>Test Videos:</strong></p>
                    <button class="test-btn" onclick="testVideo('dQw4w9WgXcQ', 'video', 'medium')">🎬 Rick Roll (Video)</button>
                    <button class="test-btn" onclick="testVideo('dQw4w9WgXcQ', 'audio')">🎵 Rick Roll (Audio)</button>
                    <button class="test-btn" onclick="testVideo('jNQXAC9IVRw', 'video', 'low')">📹 First YouTube Video</button>
                    <button class="test-btn" onclick="testVideo('9bZkp7q19f0', 'video', 'medium')">🕺 Gangnam Style</button>
                </div>
                
                <div id="player" style="display:none;">
                    <h3>Now Playing:</h3>
                    <div id="audioPlayerContainer" style="display:none;">
                        <audio id="audioPlayer" controls autoplay></audio>
                    </div>
                    <div id="videoPlayerContainer" style="display:none;">
                        <video id="videoPlayer" controls autoplay></video>
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
                    <option value="audio">Audio Only (.m4a)</option>
                    <option value="video">Video + Audio (.mp4)</option>
                </select>
                <select id="downloadQuality" style="display:none;">
                    <option value="">Auto Quality</option>
                    <option value="low">Low (360p)</option>
                    <option value="medium">Medium (720p)</option>
                    <option value="high">High (1080p)</option>
                    <option value="best">Best Available</option>
                </select>
                <button onclick="downloadMedia()">⬇ Download</button>
                <div id="downloadStatus" class="status"></div>
            </div>
            
            <!-- Info Tab -->
            <div id="info-tab" class="tab-content">
                <h3>Video Information</h3>
                <input type="text" id="infoVideoId" placeholder="dQw4w9WgXcQ" value="dQw4w9WgXcQ" style="width: 400px;">
                <button onclick="getVideoInfo()">📊 Get Info</button>
                <div id="infoResult" style="background: #f8f9fa; padding: 20px; border-radius: 10px; max-height: 400px; overflow: auto; margin-top: 15px;"></div>
            </div>
            
            <!-- API Tab -->
            <div id="api-tab" class="tab-content">
                <h3>API Documentation</h3>
                <div class="endpoint-list">
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/proxy/audio/{video_id}</code>
                        <p>Proxied audio stream (Recommended for browsers)</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/proxy/video/{video_id}</code>
                        <p>Proxied video stream (Recommended for browsers)</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/direct/audio/{video_id}</code>
                        <p>Direct audio stream URL redirect</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/direct/video/{video_id}</code>
                        <p>Direct video stream URL redirect</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/download/audio/{video_id}</code>
                        <p>Download audio file</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/download/video/{video_id}</code>
                        <p>Download video file</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/info/{video_id}</code>
                        <p>Get video information and formats</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/health</code>
                        <p>Health check endpoint</p>
                    </div>
                    <div class="endpoint">
                        <span class="method">GET</span>
                        <code>/test/{video_id}</code>
                        <p>Test yt-dlp with video</p>
                    </div>
                </div>
                
                <h4 style="margin-top: 30px;">Quality Parameters (for video):</h4>
                <ul>
                    <li><code>?quality=low</code> - 360p or lower</li>
                    <li><code>?quality=medium</code> - 720p (Default)</li>
                    <li><code>?quality=high</code> - 1080p</li>
                    <li><code>?quality=best</code> - Best available quality</li>
                </ul>
            </div>
            
            <div class="api-endpoints">
                <h3>Quick Examples:</h3>
                <p>
                    <a href="/proxy/audio/dQw4w9WgXcQ" target="_blank">🎵 Stream Rick Roll Audio</a> | 
                    <a href="/proxy/video/dQw4w9WgXcQ" target="_blank">🎬 Stream Rick Roll Video</a> | 
                    <a href="/info/dQw4w9WgXcQ" target="_blank">📊 Get Video Info</a> | 
                    <a href="/health" target="_blank">🏥 Health Check</a>
                </p>
            </div>
        </div>
        
        <script>
            function switchTab(tabName) {
                // Update tabs
                document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
                
                event.target.classList.add('active');
                document.getElementById(tabName + '-tab').classList.add('active');
                
                // Show/hide quality selector based on media type
                if (tabName === 'stream') {
                    const mediaType = document.getElementById('mediaType').value;
                    document.getElementById('quality').style.display = mediaType === 'video' ? 'inline-block' : 'none';
                } else if (tabName === 'download') {
                    const downloadType = document.getElementById('downloadType').value;
                    document.getElementById('downloadQuality').style.display = downloadType === 'video' ? 'inline-block' : 'none';
                }
            }
            
            // Toggle quality selector based on media type
            document.getElementById('mediaType').addEventListener('change', function() {
                document.getElementById('quality').style.display = this.value === 'video' ? 'inline-block' : 'none';
            });
            
            document.getElementById('downloadType').addEventListener('change', function() {
                document.getElementById('downloadQuality').style.display = this.value === 'video' ? 'inline-block' : 'none';
            });
            
            async function playStream() {
                const videoInput = document.getElementById('videoId').value;
                const mediaType = document.getElementById('mediaType').value;
                const quality = document.getElementById('quality').value;
                
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
                    let url = `/proxy/audio/${videoId}`;
                    audio.src = url;
                    
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
                    let url = `/proxy/video/${videoId}`;
                    if (quality) {
                        url += `?quality=${quality}`;
                    }
                    video.src = url;
                    
                    video.onloadeddata = () => {
                        showStatus(`Playing Video: ${videoId}${quality ? ' (' + quality + ')' : ''}`, 'success');
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
                const quality = document.getElementById('quality').value;
                const videoId = extractVideoId(videoInput);
                
                let url = `/proxy/${mediaType}/${videoId}`;
                if (mediaType === 'video' && quality) {
                    url += `?quality=${quality}`;
                }
                
                window.open(url, '_blank');
            }
            
            function testVideo(videoId, type, quality = '') {
                document.getElementById('videoId').value = videoId;
                document.getElementById('mediaType').value = type;
                if (quality && document.getElementById('quality')) {
                    document.getElementById('quality').value = quality;
                    document.getElementById('quality').style.display = 'inline-block';
                }
                playStream();
            }
            
            function downloadMedia() {
                const videoInput = document.getElementById('downloadVideoId').value;
                const type = document.getElementById('downloadType').value;
                const quality = document.getElementById('downloadQuality').value;
                
                if (!videoInput.trim()) {
                    showDownloadStatus('Please enter a video ID or URL', 'error');
                    return;
                }
                
                const videoId = extractVideoId(videoInput);
                showDownloadStatus('Starting download...', 'loading');
                
                let url = `/download/${type}/${videoId}`;
                if (type === 'video' && quality) {
                    url += `?quality=${quality}`;
                }
                
                window.open(url, '_blank');
                showDownloadStatus(`Download started for ${videoId}`, 'success');
            }
            
            async function getVideoInfo() {
                const videoInput = document.getElementById('infoVideoId').value;
                
                if (!videoInput.trim()) {
                    document.getElementById('infoResult').innerHTML = '<div class="error">Please enter a video ID or URL</div>';
                    return;
                }
                
                const videoId = extractVideoId(videoInput);
                document.getElementById('infoResult').innerHTML = '<div class="loading">Loading...</div>';
                
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
                    
                    if (data.formats_info) {
                        html += `<details><summary>Show Formats</summary><pre style="max-height: 300px; overflow: auto; background: white; padding: 10px; border-radius: 5px; margin-top: 10px;">${data.formats_info}</pre></details>`;
                    }
                    
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
            
            // Initialize quality selectors
            document.getElementById('quality').style.display = document.getElementById('mediaType').value === 'video' ? 'inline-block' : 'none';
            document.getElementById('downloadQuality').style.display = document.getElementById('downloadType').value === 'video' ? 'inline-block' : 'none';
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/proxy/{media_type}/{video_id}")
async def proxy_stream_endpoint(media_type: str, video_id: str, request: Request, quality: Optional[str] = None):
    """Proxy stream through server"""
    logger.info(f"Proxy {media_type} stream request: {video_id}, quality: {quality}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Get stream URL
    url = await get_stream_url(video_id, media_type, quality)
    
    if not url:
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Could not get {media_type} stream URL",
                "video_id": video_id,
                "quality": quality,
                "suggestions": [
                    "Try updating yt-dlp: pip install --upgrade yt-dlp",
                    "Try a different video",
                    "Try without quality parameter",
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
async def stream_direct(media_type: str, video_id: str, quality: Optional[str] = None):
    """Direct stream without token (for download or external players)"""
    logger.info(f"Direct {media_type} stream request: {video_id}, quality: {quality}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Get stream URL
    url = await get_stream_url(video_id, media_type, quality)
    
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
                "quality": quality,
                "suggestions": [
                    "Try using /proxy endpoint for browser streaming",
                    "Try updating yt-dlp",
                    "Try a different video"
                ]
            }
        )

@app.get("/download/{media_type}/{video_id}")
async def download_media(media_type: str, video_id: str, quality: Optional[str] = None):
    """Download audio or video"""
    logger.info(f"Download {media_type} request: {video_id}, quality: {quality}")
    
    if media_type not in ["audio", "video"]:
        raise HTTPException(400, "Media type must be 'audio' or 'video'")
    
    # Clean video ID
    clean_video_id = extract_video_id(video_id)
    
    url = await get_stream_url(clean_video_id, media_type, quality)
    
    if url:
        if media_type == "audio":
            filename = f"{clean_video_id}.m4a"
            content_type = "audio/mp4"
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
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"Cannot download {media_type}",
                "video_id": clean_video_id,
                "quality": quality,
                "suggestions": [
                    "Try without quality parameter",
                    "Try a different video",
                    "The video might be restricted"
                ]
            }
        )

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
        "timestamp": datetime.utcnow().isoformat(),
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
            cmd = ["yt-dlp", "--ignore-errors", "-f", fmt, "-g", f"https://youtu.be/{clean_video_id}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            
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
        "timestamp": datetime.utcnow().isoformat(),
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
        
        # System info
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "active_tokens": len(tokens),
            "cache_size": len(stream_cache),
            "audio_working": bool(audio_url),
            "video_working": bool(video_url),
            "proxies_available": len(PROXIES),
            "test_video": test_video,
            "system": {
                "cpu_percent": cpu_percent,
                "memory_percent": memory.percent,
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "disk_percent": disk.percent,
                "disk_free_gb": round(disk.free / (1024**3), 2)
            },
            "message": "API is operational. Use /proxy endpoints for browser streaming."
        }
    except Exception as e:
        return {
            "status": "degraded",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
            "message": "Some features may not work properly"
        }

@app.get("/cache/clear")
async def clear_cache():
    """Clear the stream cache"""
    stream_cache.clear()
    return {
        "success": True,
        "message": "Cache cleared",
        "cleared_entries": len(stream_cache),
        "timestamp": datetime.utcnow().isoformat()
    }

# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": str(exc.detail),
            "path": request.url.path,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "path": request.url.path,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

# Render-specific startup
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Starting server on port {port}")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port, 
        log_level="info",
        access_log=True,
        proxy_headers=True
    )