#!/bin/bash
# AutonomOS 1-Click Launch Script for macOS
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/Downloads/development/flutter/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

echo "=================================================="
echo "          🚀 Launching AutonomOS Desktop          "
echo "=================================================="

# 1. Start Python Backend Server
SERVER_PID=""
if ! lsof -i :8000 >/dev/null 2>&1; then
    echo "▶ Starting AutonomOS backend server (port 8000)..."
    cd "$DIR"
    python3 app/server.py --port 8000 --db "$DIR/autonomos.db" > "$DIR/server.log" 2>&1 &
    SERVER_PID=$!
    
    # Wait for server /health
    for i in {1..30}; do
        if curl -s http://127.0.0.1:8000/health >/dev/null 2>&1; then
            echo "✔ Backend server active and listening on http://127.0.0.1:8000"
            break
        fi
        sleep 0.1
    done
else
    echo "✔ Backend server already running on port 8000"
fi

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        echo "⏹ Shutting down AutonomOS background server..."
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 2. Launch Flutter macOS App
RELEASE_APP="$DIR/client/build/macos/Build/Products/Release/client.app"

# If Release app does not exist or user wants latest, run flutter build or run
if [ -d "$RELEASE_APP" ]; then
    echo "▶ Opening AutonomOS GUI..."
    open -W "$RELEASE_APP"
else
    echo "▶ Running latest Flutter macOS app..."
    cd "$DIR/client"
    flutter run -d macos
fi

echo "✔ AutonomOS closed successfully."
