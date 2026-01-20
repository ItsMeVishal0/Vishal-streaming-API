#!/bin/bash

set -e

echo "🚀 Starting YouTube Streaming API on Render..."

echo "📁 Creating directories..."
mkdir -p downloads logs static

if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  FFmpeg not found. Audio conversion may be limited."
fi

echo "🐍 Python version: $(python --version)"

echo "📦 Upgrading pip..."
pip install --upgrade pip

echo "📦 Installing dependencies..."
pip install -r requirements.txt

if [ -f "cookies.txt" ]; then
    echo "🍪 Cookies file detected"
    if head -1 cookies.txt | grep -q "Netscape HTTP Cookie File"; then
        echo "   ✅ Valid cookies format"
    else
        echo "   ⚠️  Cookies file may not be in Netscape format"
    fi
else
    echo "⚠️  No cookies.txt file found"
fi

echo "🔧 Environment check:"
echo "   HOST: ${HOST:-0.0.0.0}"
echo "   PORT: ${PORT:-8000}"
echo "   DEBUG: ${DEBUG:-false}"
echo "   LOG_LEVEL: ${LOG_LEVEL:-INFO}"

echo "🔒 Setting permissions..."
chmod -R 755 downloads logs static

if [ ! -f "static/favicon.ico" ]; then
    echo "🎨 Creating default favicon..."
    echo -ne '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x00\r\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82' > static/favicon.ico
fi

echo "=============================================="
echo "🚀 Starting FastAPI application..."
echo "🌐 Server will be available at: http://0.0.0.0:${PORT:-8000}"
echo "📚 API Docs: http://0.0.0.0:${PORT:-8000}/docs"
echo "📊 Health: http://0.0.0.0:${PORT:-8000}/health"
echo "=============================================="

exec python main.py