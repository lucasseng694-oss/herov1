@echo off
title Hero SMS Automation Launcher

echo ===================================================
echo   HERO SMS AUTOMATION - PORTABLE LAUNCHER
echo ===================================================

REM 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed on this PC.
    echo Downloading and installing Python 3.11 quietly...
    curl -L -o python_installer.exe https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
    start /wait python_installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del python_installer.exe
)

REM 2. Create local virtual environment if it doesn't exist
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM 3. Activate virtual environment and install packages
echo Activating environment and verifying dependencies...
call .venv\Scripts\activate.bat

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

REM 4. Verify/install Playwright browsers
echo Verifying Playwright browser drivers...
playwright install chromium

REM 5. Launch the Flask Web Application & Auto-Open Browser
echo ===================================================
echo   Setup complete! Launching Web UI Dashboard...
echo ===================================================
echo   Opening http://127.0.0.1:5000 in your browser...
echo ===================================================
start "" "http://127.0.0.1:5000"
python app.py

pause
