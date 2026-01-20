#!/bin/bash

# Advanced YouTube Streaming API Startup Script for Render

set -e  # Exit on error

echo "🚀 Starting YouTube Streaming API on Render..."
echo "=============================================="

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p downloads logs static

# Install FFmpeg if not present (Render usually has it)
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  FFmpeg not found. Audio conversion may be limited."
    echo "   Consider using a Dockerfile with FFmpeg pre-installed."
fi

# Check Python version
echo "🐍 Python version: $(python --version)"

# Install/upgrade pip
echo "📦 Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Check for cookies file
if [ -f "cookies.txt" ]; then
    echo "🍪 Cookies file detected"
    # Validate cookies format
    if head -1 cookies.txt | grep -q "Netscape HTTP Cookie File"; then
        echo "   ✅ Valid cookies format"
    else
        echo "   ⚠️  Cookies file may not be in Netscape format"
    fi
else
    echo "⚠️  No cookies.txt file found"
    echo "   Age-restricted videos may not work"
fi

# Check environment variables
echo "🔧 Environment check:"
echo "   HOST: ${HOST:-0.0.0.0}"
echo "   PORT: ${PORT:-8000}"
echo "   DEBUG: ${DEBUG:-false}"
echo "   LOG_LEVEL: ${LOG_LEVEL:-INFO}"

# Set proper permissions
echo "🔒 Setting permissions..."
chmod -R 755 downloads logs static

# Create default static files if not exists
if [ ! -f "static/favicon.ico" ]; then
    echo "🎨 Creating default favicon..."
    # Create a simple favicon (1x1 transparent PNG)
    echo -ne '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x00\r\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82' > static/favicon.ico
fi

# Health check endpoint test
echo "🏥 Testing health endpoint..."
python -c "
import sys
sys.path.append('.')
from config import config
print(f'   Config loaded: PORT={config.PORT}, DEBUG={config.DEBUG}')
"

# Start the application
echo "=============================================="
echo "🚀 Starting FastAPI application..."
echo "🌐 Server will be available at: http://0.0.0.0:${PORT:-8000}"
echo "📚 API Docs: http://0.0.0.0:${PORT:-8000}/docs"
echo "📊 Health: http://0.0.0.0:${PORT:-8000}/health"
echo "=============================================="

# Run the application
exec python main.py