#!/bin/bash
set -e

uv run uvicorn computer_use_demo.api:app --host 0.0.0.0 --port 8080 > /tmp/api_stdout.log &

echo "✨ Computer Use Demo (FastAPI backend) is ready!"
echo "➡️  Open http://localhost:3000 in your browser to access the frontend"

# Keep the container running
tail -f /dev/null
