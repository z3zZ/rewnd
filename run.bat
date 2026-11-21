@echo off
REM Windows: sets up a local virtual environment on first run, then launches rewnd.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up rewnd, this takes a minute...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m rewnd %*
