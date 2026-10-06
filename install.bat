@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo    OCR program setup
echo ============================================
echo.

echo [1/3] Creating virtual environment (venv)...
python -m venv venv
if errorlevel 1 (
  echo.
  echo [ERROR] venv creation failed. Is Python installed?
  echo   Install Python from https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [2/3] Upgrading pip...
call "venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Installing packages... (several hundred MB, a few minutes)
call "venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERROR] Package install failed. Check your internet connection.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   Setup complete!  Run  run.bat  to start.
echo ============================================
pause
