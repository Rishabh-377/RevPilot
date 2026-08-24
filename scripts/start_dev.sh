#!/usr/bin/env bash
# =============================================================================
# RevPilot Persistent Development Server Startup Script
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT=8000
HOST="127.0.0.1"
LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/revpilot_dev.log"
PID_FILE="$LOG_DIR/revpilot_dev.pid"
HEALTH_URL="http://${HOST}:${PORT}/api/v1/health"

mkdir -p "$LOG_DIR"

# 1. Check if port is already active and healthy
if lsof -ti ":${PORT}" > /dev/null 2>&1; then
    RUNNING_PID=$(lsof -ti ":${PORT}" | head -n 1)
    if curl -s -f "$HEALTH_URL" > /dev/null 2>&1; then
        echo "✅ RevPilot dev server is ALREADY running and healthy."
        echo "   PID:       $RUNNING_PID"
        echo "   Port:      $PORT"
        echo "   Dashboard: http://localhost:${PORT}/dashboard"
        echo "$RUNNING_PID" > "$PID_FILE"
        exit 0
    else
        echo "⚠️ Port $PORT is occupied by PID $RUNNING_PID, but not responding to health check. Attempting cleanup..."
        kill -15 "$RUNNING_PID" 2>/dev/null || true
        sleep 1
    fi
fi

# 2. Activate virtual environment if present
if [ -d "$ROOT_DIR/.venv" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
fi

# 3. Start uvicorn in detached background mode
echo "🚀 Starting RevPilot development server..."
nohup python3 -m uvicorn backend.main:app --host "$HOST" --port "$PORT" --reload > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# 4. Wait for health check response (up to 10s)
MAX_WAIT=10
WAITED=0
HEALTHY=false

while [ "$WAITED" -lt "$MAX_WAIT" ]; do
    if curl -s -f "$HEALTH_URL" > /dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ "$HEALTHY" = true ]; then
    echo "✅ RevPilot dev server started successfully in persistent background mode."
    echo "   PID:       $SERVER_PID"
    echo "   Host:      $HOST"
    echo "   Port:      $PORT"
    echo "   Logs:      $LOG_FILE"
    echo "   Dashboard: http://localhost:${PORT}/dashboard"
    echo "   Health:    $HEALTH_URL"
    exit 0
else
    echo "❌ Server failed to respond to health check within ${MAX_WAIT}s."
    echo "   Check logs at $LOG_FILE"
    exit 1
fi
