#!/usr/bin/env bash
# Linux / macOS: sets up a local virtual environment on first run, then launches rewnd.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up rewnd (this takes a minute)…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m rewnd "$@"
