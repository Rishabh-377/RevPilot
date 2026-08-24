#!/usr/bin/env bash
# =============================================================================
# RevPilot Development Server Stop Script
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000
PID_FILE="$ROOT_DIR/logs/revpilot_dev.pid"

STOPPED=false

# 1. Stop via PID file if valid
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" || true)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "🛑 Stopping RevPilot dev server (PID: $PID)..."
        kill -15 "$PID" 2>/dev/null || true
        
        # Wait up to 5 seconds for graceful shutdown
        for _ in {1..5}; do
            if ! kill -0 "$PID" 2>/dev/null; then
                STOPPED=true
                break
            fi
            sleep 1
        done

        if [ "$STOPPED" = false ]; then
            echo "⚠️ Process $PID did not stop gracefully. Forcing termination..."
            kill -9 "$PID" 2>/dev/null || true
            STOPPED=true
        fi
    fi
    rm -f "$PID_FILE"
fi

# 2. Stop any remaining process specifically listening on port 8000
if lsof -ti ":${PORT}" > /dev/null 2>&1; then
    PORT_PIDS=$(lsof -ti ":${PORT}")
    for P in $PORT_PIDS; do
        # Verify it is a python/uvicorn process before terminating
        PROC_CMD=$(ps -p "$P" -o command= 2>/dev/null || true)
        if [[ "$PROC_CMD" =~ "uvicorn" ]] || [[ "$PROC_CMD" =~ "backend.main:app" ]]; then
            echo "🛑 Stopping RevPilot listener on port $PORT (PID: $P)..."
            kill -15 "$P" 2>/dev/null || true
            sleep 1
            kill -9 "$P" 2>/dev/null || true
            STOPPED=true
        fi
    done
fi

if [ "$STOPPED" = true ]; then
    echo "✅ RevPilot dev server stopped cleanly."
else
    echo "ℹ️ No running RevPilot server was found on port $PORT."
fi
