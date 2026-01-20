import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Rate limiting (Render optimized)
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "10"))
    MAX_REQUESTS_PER_MINUTE: int = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "30"))
    
    # YouTube settings
    YTDLP_TIMEOUT: int = 60
    COOKIES_FILE: Optional[str] = "cookies.txt" if os.path.exists("cookies.txt") else None
    
    # Cache settings (Render optimized - less memory)
    CACHE_TTL: int = 3600  # 1 hour
    MAX_CACHE_SIZE: int = int(os.getenv("MAX_CACHE_SIZE", "500"))
    
    # Proxy settings
    PROXY: Optional[str] = os.getenv("PROXY", None)
    
    # Download settings (Render has memory limits)
    DOWNLOAD_DIR: str = "downloads"
    MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE_MB", "100")) * 1024 * 1024
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = "logs/streaming_api.log"
    
    # YouTube API settings (optional)
    YOUTUBE_API_KEY: Optional[str] = os.getenv("YOUTUBE_API_KEY", None)
    
    # Advanced settings for Render
    ENABLE_METRICS: bool = True
    ENABLE_HEALTH_CHECK: bool = True
    ENABLE_CACHE_STATS: bool = True
    
    # Enhanced yt-dlp options for Render
    YTDLP_DEFAULT_OPTS: dict = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 45,
        'retries': 5,
        'fragment_retries': 5,
        'skip_unavailable_fragments': True,
        'ignoreerrors': True,
        'nooverwrites': True,
        'concurrent_fragment_downloads': 2,
        'throttledratelimit': 512000,  # 500KB/s
        'sleep_interval_requests': 2,
        'sleep_interval': 3,
        'max_sleep_interval': 15,
        'verbose': False,
        'force_generic_extractor': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web', 'ios'],
                'player_skip': ['configs', 'webpage'],
            }
        },
        'format_sort': ['+acodec', '+vcodec', 'res', 'fps', 'size', 'br', 'asr'],
        'check_formats': 'selected',
        'compat_opts': ['no-youtube-unavailable-videos'],
        'geo_bypass': True,
        'geo_bypass_country': 'US',
    }

config = Config()

# Create directories
os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("static", exist_ok=True)