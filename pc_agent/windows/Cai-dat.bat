@echo off
cd /d "%~dp0"
echo.
echo Cai phan mem ket noi PC voi hub.
echo Dan token hien tren trang tai, roi nhan Enter.
echo.
set /p CONTROL_TOKEN=Token: 
if "%CONTROL_TOKEN%"=="" (
  echo Chua co token.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-VideoWorker.ps1"
echo.
pause
