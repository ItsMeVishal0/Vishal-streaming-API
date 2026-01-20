import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Rate limiting
    RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "10"))
    MAX_REQUESTS_PER_MINUTE: int = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "30"))
    
    # YouTube settings
    YTDLP_TIMEOUT: int = 60
    COOKIES_FILE: Optional[str] = "cookies.txt" if os.path.exists("cookies.txt") else None
    
    # Cache settings
    CACHE_TTL: int = 3600
    MAX_CACHE_SIZE: int = int(os.getenv("MAX_CACHE_SIZE", "500"))
    
    # Proxy settings
    PROXY: Optional[str] = os.getenv("PROXY", None)
    
    # Download settings
    DOWNLOAD_DIR: str = "downloads"
    MAX_DOWNLOAD_SIZE: int = int(os.getenv("MAX_DOWNLOAD_SIZE_MB", "100")) * 1024 * 1024
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str = "logs/streaming_api.log"
    
    # YouTube API settings (optional)
    YOUTUBE_API_KEY: Optional[str] = os.getenv("YOUTUBE_API_KEY", None)

config = Config()

# Create directories
os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("static", exist_ok=True)