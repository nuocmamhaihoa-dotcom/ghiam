@echo off
chcp 65001 >nul
title FbPoller
set "CONTROL_HUB=__CONTROL_HUB__"
set "CONTROL_TOKEN=__CONTROL_TOKEN__"
set "ROOT=%LOCALAPPDATA%\FbPollerVideo"
if not exist "%ROOT%" mkdir "%ROOT%"
:again
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=Join-Path $env:LOCALAPPDATA 'FbPollerVideo'; New-Item -ItemType Directory -Force -Path $d | Out-Null; Invoke-WebRequest -UseBasicParsing -Uri ($env:CONTROL_HUB.TrimEnd('/')+'/cai-video-open.ps1') -OutFile (Join-Path $d 'Open-FbPoller.ps1')"
if errorlevel 1 goto retry
powershell -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\FbPollerVideo\Open-FbPoller.ps1"
if errorlevel 1 goto retry
exit /b 0
:retry
echo Chua noi duoc. Thu lai sau 5 giay.
timeout /t 5 /nobreak >nul
goto again
