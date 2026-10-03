#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
PORT="${PORT:-3001}"
exec "$PY" -m uvicorn agent.main:app --host 0.0.0.0 --port "$PORT"
