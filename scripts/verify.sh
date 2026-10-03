#!/usr/bin/env bash
# Runs `python -m agent.verify`, preferring the project virtualenv.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
exec "$PY" -m agent.verify "$@"
