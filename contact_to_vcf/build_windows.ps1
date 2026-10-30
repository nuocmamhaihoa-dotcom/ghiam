# Build ContactToVCF.exe on Windows. Python 3.12+ is required on the build machine.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

py -3.12 -m pip install -r contact_to_vcf\requirements.txt pyinstaller
py -3.12 -m PyInstaller --noconfirm contact_to_vcf\ContactToVCF.spec

$Release = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $Release | Out-Null
Copy-Item -Force dist\ContactToVCF.exe (Join-Path $Release "ContactToVCF.exe")
Copy-Item -Force contact_to_vcf\release\README.txt (Join-Path $Release "README.txt")
Write-Host "Da tao release\ContactToVCF.exe"
