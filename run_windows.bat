@echo off
rem Double-click to run AI Fishing. The first run installs everything it needs (needs Python 3.9+).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up, this takes a minute...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during setup, then run this again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause & exit /b 1)
)
".venv\Scripts\python.exe" ai_fishing.py %*
pause
