#!/bin/bash
# Arquant v1.0 - Stop Script

echo "🛑 Stopping Arquant Web Server..."
lsof -ti:8500 | xargs kill -9 2>/dev/null

echo "🛑 Stopping Cloudflare Tunnel..."
pkill -f "cloudflared tunnel" 2>/dev/null

echo "✅ All services stopped."
