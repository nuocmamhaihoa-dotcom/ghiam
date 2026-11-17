@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "DANHBA_KHO=%~dp0kho"
set "DANHBA_LOCAL=1"
set "DANHBA_HOST=127.0.0.1"
set "DANHBA_PORT=8080"
set "PYTHONPATH=%~dp0vendor;%~dp0contact_to_vcf\src"
set "APP_DIR=%~dp0"
set "APP_BAT=%~dp0Mo-Danh-Ba.bat"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w = New-Object -ComObject WScript.Shell; $s = $w.CreateShortcut($w.SpecialFolders('Desktop') + '\Danh ba.lnk'); $s.TargetPath = $env:APP_BAT; $s.WorkingDirectory = $env:APP_DIR; $s.Description = 'Danh ba tren may tinh'; $s.Save()" >nul 2>&1
echo Dang mo Danh ba tren may tinh nay.
echo Trinh duyet chi mo http://127.0.0.1:8080/
echo Lan sau hay bam Danh ba tren man hinh nen.
echo Dong cua so nay de tat phan mem.
"%~dp0python\python.exe" "%~dp0contact_to_vcf\src\web\pc_launcher.py"
if errorlevel 1 (
  echo.
  echo Khong mo duoc phan mem tren may tinh nay.
  echo Hay chay lai file cai dat va cho den khi cua so nay hien ra.
  pause
)
