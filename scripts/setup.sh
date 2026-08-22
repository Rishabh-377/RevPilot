#!/usr/bin/env bash
set -euo pipefail

echo "========================================="
echo " RevPilot Environment Setup"
echo "========================================="

if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required but not installed." >&2
    exit 1
fi

echo "[1/3] Creating virtual environment (.venv)..."
python3 -m venv .venv
source .venv/bin/activate

echo "[2/3] Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    echo "[3/3] Creating .env from .env.example..."
    cp .env.example .env
fi

echo "========================================="
echo "✅ Setup complete! Run 'source .venv/bin/activate' then 'pytest -v'."
echo "========================================="
