@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
  echo Please run  install.bat  first to complete setup.
  pause
  exit /b 1
)

start "" "venv\Scripts\pythonw.exe" ocr_app.py
