#!/bin/bash
cd "$(dirname "$0")"

# Auto-rebuild venv if missing or broken (e.g. after moving the folder)
if ! venv/bin/uvicorn --version >/dev/null 2>&1; then
    echo "Setting up environment (first run or folder was moved)..."
    rm -rf venv
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
fi

source venv/bin/activate
(sleep 2 && open http://localhost:8000) &
uvicorn main:app