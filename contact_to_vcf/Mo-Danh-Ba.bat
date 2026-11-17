@echo off
cd /d "%~dp0"
set "DANHBA_KHO=%~dp0kho"
set "DANHBA_HOST=127.0.0.1"
set "DANHBA_PORT=8080"
set "PYTHONPATH=%~dp0vendor;%~dp0contact_to_vcf\src"
echo Dang mo Danh ba tren may tinh nay...
echo Trinh duyet se mo http://127.0.0.1:8080/
echo Dong cua so nay de tat phan mem.
"%~dp0python\python.exe" "%~dp0contact_to_vcf\src\web\pc_launcher.py"
if errorlevel 1 (
  echo.
  echo Khong mo duoc. Hay giai nen ca thu muc roi bam Mo-Danh-Ba.bat ben trong thu muc do.
  pause
)
