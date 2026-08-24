#!/usr/bin/env bash
# =============================================================================
# RevPilot Development Server Status Script
# =============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000
HOST="127.0.0.1"
HEALTH_URL="http://${HOST}:${PORT}/api/v1/health"
DASHBOARD_URL="http://localhost:${PORT}/dashboard"
PID_FILE="$ROOT_DIR/logs/revpilot_dev.pid"

RUNNING=false
ACTIVE_PID=""

# 1. Determine active PID
if [ -f "$PID_FILE" ]; then
    RECORDED_PID=$(cat "$PID_FILE" || true)
    if [ -n "$RECORDED_PID" ] && kill -0 "$RECORDED_PID" 2>/dev/null; then
        ACTIVE_PID="$RECORDED_PID"
        RUNNING=true
    fi
fi

if [ -z "$ACTIVE_PID" ] && lsof -ti ":${PORT}" > /dev/null 2>&1; then
    PORT_PID=$(lsof -ti ":${PORT}" | head -n 1)
    PROC_CMD=$(ps -p "$PORT_PID" -o command= 2>/dev/null || true)
    if [[ "$PROC_CMD" =~ "uvicorn" ]] || [[ "$PROC_CMD" =~ "backend.main:app" ]]; then
        ACTIVE_PID="$PORT_PID"
        RUNNING=true
    fi
fi

# 2. Perform health check & report
if [ "$RUNNING" = true ] && [ -n "$ACTIVE_PID" ]; then
    HEALTH_RESP=$(curl -s -f "$HEALTH_URL" 2>/dev/null || true)
    if [ -n "$HEALTH_RESP" ]; then
        echo "================================================================="
        echo "🟢 REVPILOT DEV SERVER: RUNNING (HEALTHY)"
        echo "================================================================="
        echo "PID:         $ACTIVE_PID"
        echo "Port:        $PORT"
        echo "Dashboard:   $DASHBOARD_URL"
        echo "Health URL:  $HEALTH_URL"
        echo "Health Resp: $HEALTH_RESP"
        echo "================================================================="
        exit 0
    else
        echo "================================================================="
        echo "🟡 REVPILOT DEV SERVER: PROCESS ALIVE BUT NOT RESPONDING TO HEALTH"
        echo "================================================================="
        echo "PID:         $ACTIVE_PID"
        echo "Port:        $PORT"
        echo "Dashboard:   $DASHBOARD_URL"
        echo "Health URL:  $HEALTH_URL"
        echo "================================================================="
        exit 1
    fi
else
    echo "================================================================="
    echo "🔴 REVPILOT DEV SERVER: STOPPED"
    echo "================================================================="
    echo "Port:        $PORT (Idle)"
    echo "================================================================="
    exit 1
fi
