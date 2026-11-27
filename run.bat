@echo off
REM Windows: sets up a local virtual environment on first run, then launches rewnd.
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up rewnd, this takes a minute...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip >nul
  .venv\Scripts\python -m pip install -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)

REM New requirements since the last run (after an update): try to install them. A failed update
REM (offline, say) never stops rewnd starting, as long as what's installed can still run it.
fc /b requirements.txt .venv\requirements.installed >nul 2>&1 && goto launch
echo Updating rewnd's packages...
.venv\Scripts\python -m pip install --disable-pip-version-check --retries 1 --timeout 10 -r requirements.txt >> .venv\update.log 2>&1
if not errorlevel 1 (
  copy /y requirements.txt .venv\requirements.installed >nul
  goto launch
)
.venv\Scripts\python -c "import rewnd.app" >nul 2>&1
if errorlevel 1 (
  echo rewnd's packages are missing and couldn't be installed. Check your internet connection and run it again.
  echo Details: .venv\update.log
  >> .venv\update.log echo [%date% %time%] ERROR: package install failed and rewnd can't start without it
  pause
  exit /b 1
)
echo WARNING: couldn't update rewnd's packages, probably offline. Starting anyway; it tries again next launch.
echo Details: .venv\update.log
>> .venv\update.log echo [%date% %time%] WARNING: package update failed; launched with the packages already installed

:launch
start "" .venv\Scripts\pythonw -m rewnd %*
