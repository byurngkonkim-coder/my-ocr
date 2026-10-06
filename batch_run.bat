@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist "venv\Scripts\python.exe" (
  echo [!] Please run install.bat first to complete setup.
  pause
  exit /b 1
)

rem --- Folder comes from a dragged-and-dropped argument, or a picker dialog ---
set "TARGET=%~1"

if not defined TARGET (
  echo Opening folder selection window...
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='Select the image folder'; if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){[Console]::Out.Write($d.SelectedPath)}"`) do set "TARGET=%%I"
)

if not defined TARGET (
  echo No folder selected. Tip: you can also drag an image folder onto this file.
  pause
  exit /b 1
)

echo.
echo Target: "%TARGET%"
echo Processing... this can take a while for many images.
echo.
"venv\Scripts\python.exe" batch_folder.py "%TARGET%"
echo.
pause
