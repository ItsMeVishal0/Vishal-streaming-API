#!/bin/bash

echo "🚀 Starting YouTube Stream API on Render..."

# Create data directory
mkdir -p /tmp/data

# Install yt-dlp if not present
if ! command -v yt-dlp &> /dev/null; then
    echo "📦 Installing yt-dlp..."
    pip install yt-dlp
fi

# Start the server
exec uvicorn api:app \
    --host 0.0.0.0 \
    --port ${PORT:-8080} \
    --workers 2 \
    --log-level info \
    --access-log \
    --proxy-headers