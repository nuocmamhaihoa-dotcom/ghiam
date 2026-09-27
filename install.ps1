# Cài fb-poller PA1 trên Windows (PowerShell)
# Chạy: powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[install] Cài fb-poller PA1 trên Windows..." -ForegroundColor Green

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Host "Cần Python 3.11+ trong PATH. Tải tại https://www.python.org/downloads/" -ForegroundColor Red
  exit 1
}

$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$workers = [math]::Max(1, [math]::Min(14, [int]($cores * 0.7)))
$workerId = "$env:COMPUTERNAME-1"

Write-Host "[install] CPU=$cores RAM=${ramGb}GB -> WORKERS=$workers"

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -U pip wheel
& .\.venv\Scripts\pip.exe install -e .
& .\.venv\Scripts\playwright.exe install chromium

New-Item -ItemType Directory -Force -Path data, logs, data\metrics | Out-Null
if (-not (Test-Path data\posts.txt)) { New-Item data\posts.txt -ItemType File | Out-Null }
if (-not (Test-Path data\proxies_static.txt)) { New-Item data\proxies_static.txt -ItemType File | Out-Null }
if (-not (Test-Path data\proxies_4g.txt)) { New-Item data\proxies_4g.txt -ItemType File | Out-Null }

$dbPath = (Join-Path $Root "data\fb_poller.db") -replace '\\', '/'
@"
DATABASE_URL=sqlite+aiosqlite:///$dbPath
WORKER_ID=$workerId
ROLE=all
WORKERS=$workers
WARM_COLD_MAX_INFLIGHT=3
HOT_SIZE=100
HOT_INTERVAL_SEC=45
WARM_INTERVAL_SEC=150
COLD_INTERVAL_SEC=600
BOOST_ON_NEW_SEC=1200
HOT_JOB_TIMEOUT_MS=7000
HOT_MAX_COMMENTS_PAGE=20
BROWSER_RESTART_EVERY_JOBS=80
HEADLESS=true
NAVIGATION_TIMEOUT_MS=15000
PROXIES_STATIC_FILE=$Root\data\proxies_static.txt
PROXIES_4G_FILE=$Root\data\proxies_4g.txt
DATA_DIR=$Root\data
"@ | Set-Content -Path .env -Encoding UTF8

& .\.venv\Scripts\fb-poller.exe init-db

Write-Host ""
Write-Host "Cài xong." -ForegroundColor Green
Write-Host "1) Dán URL vào data\posts.txt"
Write-Host "2) Dán proxy vào data\proxies_static.txt"
Write-Host "3) Import + chạy:"
Write-Host "   .\.venv\Scripts\Activate.ps1"
Write-Host "   fb-poller import-urls data\posts.txt"
Write-Host "   fb-poller import-proxies"
Write-Host "   fb-poller rebalance-hot"
Write-Host "   fb-poller run"
Write-Host "Hoặc dùng: .\scripts\fb-poller-ctl.ps1 start"
Write-Host ""
Write-Host "Khuyến nghị production Windows (tự chạy + tự cập nhật):" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 -StartNow"
Write-Host "Xem docs\AGENT_WINDOWS.md"
