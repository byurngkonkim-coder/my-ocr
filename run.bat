@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "TARGET=ocr_app.py"
if exist "engine\ocr_app.py" set "TARGET=engine\ocr_app.py"

set "PY="
if exist "engine\venv\Scripts\python.exe" set "PY=engine\venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"

set "PYW="
if exist "engine\venv\Scripts\pythonw.exe" set "PYW=engine\venv\Scripts\pythonw.exe"
if not defined PYW if exist "venv\Scripts\pythonw.exe" set "PYW=venv\Scripts\pythonw.exe"

if defined PYW (
    start "" "%PYW%" -X utf8 %TARGET% %*
) else if defined PY (
    start "" "%PY%" -X utf8 %TARGET% %*
) else (
    echo [!] 가상환경^(venv^)이 없습니다. engine\install.bat 을 먼저 실행해주세요.
    pause
    exit /b 1
)
endlocal
