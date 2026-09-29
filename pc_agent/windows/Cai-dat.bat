@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-VideoWorker.ps1"
if errorlevel 1 (
  echo.
  echo Cai chua xong.
  pause
  exit /b 1
)
exit /b 0
