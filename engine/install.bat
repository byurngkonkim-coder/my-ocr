@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo    OCR 프로그램 설치 / 가상환경(venv) 구축
echo ============================================
echo.

echo [1/3] 가상환경(venv) 생성 중...
python -m venv venv
if errorlevel 1 (
  echo.
  echo [오류] venv 생성 실패. Python 3.8~3.12가 설치되어 있는지 확인하세요.
  echo   https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [2/3] pip 업그레이드 중...
call "venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] 패키지 설치 중... (수 분 소요)
call "venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [오류] 패키지 설치 실패. 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   설치 완료! 최상위 폴더의 run.bat 을 실행하세요.
echo ============================================
pause
