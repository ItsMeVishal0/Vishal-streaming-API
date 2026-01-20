#!/bin/bash

set -e

echo "🚀 Starting YouTube Streaming API..."

echo "📁 Creating directories..."
mkdir -p downloads logs static

echo "🐍 Python version: $(python --version)"

echo "📦 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

if [ -f "cookies.txt" ]; then
    echo "🍪 Cookies file detected"
else
    echo "⚠️  No cookies.txt file found"
fi

echo "🔧 Environment:"
echo "   HOST: ${HOST:-0.0.0.0}"
echo "   PORT: ${PORT:-8000}"
echo "   DEBUG: ${DEBUG:-false}"

echo "=============================================="
echo "🌐 Server: http://0.0.0.0:${PORT:-8000}"
echo "📚 Test: curl http://0.0.0.0:${PORT:-8000}/health"
echo "=============================================="

exec python main.py