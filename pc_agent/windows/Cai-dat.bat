@echo off
cd /d "%~dp0"
if exist "%~dp0FbPoller.bat" (
  call "%~dp0FbPoller.bat"
  exit /b %ERRORLEVEL%
)
echo Mo FbPoller.bat tai tu trang hub.
pause
exit /b 1
