@echo off
rem Ephemeris on Windows -> http://localhost:5055
rem Double-click this file. First run creates .\venv and installs requirements.txt (needs internet, 1-2 min).
rem Keep this window open: closing it stops Ephemeris.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Python reads/writes every file as UTF-8 (the Chinese Windows default is GBK)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%PORT%"=="" set PORT=5055

if not exist "venv\Scripts\python.exe" (
  echo First run: installing what Ephemeris needs, 1-2 minutes...
  where py >nul 2>nul
  if errorlevel 1 (python -m venv venv) else (py -3 -m venv venv)
  if not exist "venv\Scripts\python.exe" (
    echo.
    echo Python 3.9+ was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" on the first screen, then run this file again.
    pause
    exit /b 1
  )
  "venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  "venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo Installing failed. Check the internet connection and run this file again.
    pause
    exit /b 1
  )
)

rem Already running: just open the page
curl -s -o nul "http://127.0.0.1:%PORT%/api/setup" >nul 2>nul
if %errorlevel%==0 (
  start "" "http://localhost:%PORT%"
  exit /b 0
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 3; Start-Process 'http://localhost:%PORT%'"
"venv\Scripts\python.exe" app.py
pause
