#!/usr/bin/env bash
set -euo pipefail

if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

echo "🚀 Starting RevPilot on http://localhost:8000/dashboard ..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
