@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PY="
if exist "engine\venv\Scripts\python.exe" set "PY=engine\venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"

if not defined PY (
  echo [!] 가상환경^(venv^)이 없습니다. engine\install.bat 을 먼저 실행해주세요.
  pause
  exit /b 1
)

set "TARGET_SCRIPT=batch_folder.py"
if exist "engine\batch_folder.py" set "TARGET_SCRIPT=engine\batch_folder.py"

rem --- 폴더 인자(드래그앤드롭) 또는 선택 대화상자 ---
set "TARGET=%~1"

if not defined TARGET (
  echo 이미지 폴더 선택 창을 엽니다...
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='이미지가 들어있는 폴더를 선택하세요'; if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.Write($d.SelectedPath)}"`) do set "TARGET=%%I"
)

if not defined TARGET (
  echo 폴더가 선택되지 않았습니다. ^(팁: 폴더를 batch_run.bat 위에 직접 드래그앤드롭해도 됩니다^)
  pause
  exit /b 1
)

echo.
echo 대상 폴더: "%TARGET%"
echo 표 일괄 추출 처리 중... (이미지가 많으면 수 분 소요될 수 있습니다)
echo.
"%PY%" %TARGET_SCRIPT% "%TARGET%"
echo.
pause
endlocal
