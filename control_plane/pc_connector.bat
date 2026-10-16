@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "RUNNER=py -3"
) else (
  set "RUNNER=python"
)
:again
%RUNNER% "%~dp0ghiam-pc.py"
if errorlevel 2 goto again
echo.
echo Dong cua so nay la PC ngung giup doc. Mo lai bang cach bam dup file nay.
pause
