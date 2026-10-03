#!/usr/bin/env bash
# Start the FastAPI backend on :3001 and the Vite client on :5173 together.
set -euo pipefail
cd "$(dirname "$0")/.."
"./scripts/server.sh" &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT INT TERM
sleep 1
npm --prefix client run dev
