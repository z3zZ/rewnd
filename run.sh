#!/usr/bin/env bash
# Linux / macOS: sets up a local virtual environment on first run, then launches rewnd.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up rewnd (this takes a minute)…"
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  cp requirements.txt .venv/requirements.installed
fi

# New requirements since the last run (after an update): try to install them. A failed update
# (offline, say) never stops rewnd starting, as long as what's installed can still run it.
if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "Updating rewnd's packages…"
  if .venv/bin/python -m pip install --disable-pip-version-check --retries 1 --timeout 10 \
       -r requirements.txt >>.venv/update.log 2>&1; then
    cp requirements.txt .venv/requirements.installed
  elif .venv/bin/python -c "import rewnd.app" >/dev/null 2>&1; then
    echo "WARNING: couldn't update rewnd's packages, probably offline. Starting anyway; it tries again next launch." >&2
    echo "Details: .venv/update.log" >&2
    echo "[$(date)] WARNING: package update failed; launched with the packages already installed" >>.venv/update.log
  else
    echo "rewnd's packages are missing and couldn't be installed. Check your internet connection and run ./run.sh again." >&2
    echo "Details: .venv/update.log" >&2
    echo "[$(date)] ERROR: package install failed and rewnd can't start without it" >>.venv/update.log
    exit 1
  fi
fi
exec .venv/bin/python -m rewnd "$@"
