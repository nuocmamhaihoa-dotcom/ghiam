# Cài một lần trên Windows PC:
# - Python venv + fb-poller
# - Agent tự chạy khi đăng nhập
# - Tự cập nhật sau này (không cần cài lại)
[CmdletBinding()]
param(
  [string]$ManifestUrl = "",
  [string]$ControlUrl = "",
  [string]$ControlToken = "",
  [int]$Workers = 0,
  [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\Common.ps1")
$AgentRoot = Get-AgentRoot
$InstallRoot = Get-InstallRoot

Write-Host "=== Cài FbPoller Agent (Windows) ===" -ForegroundColor Green
Write-Host "InstallRoot = $InstallRoot"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Cần Python 3.11+ trong PATH. https://www.python.org/downloads/ (tick Add to PATH)"
}

$cores = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
if ($Workers -le 0) {
  $Workers = [math]::Max(1, [math]::Min(14, [int]($cores * 0.7)))
}
$workerId = "$env:COMPUTERNAME-1"
$machineId = Get-OrCreateMachineId -AgentRoot $AgentRoot

# 1) Python app
Write-Host "[1/5] Cài venv + fb-poller + Chromium..."
Set-Location $InstallRoot
python -m venv (Join-Path $InstallRoot ".venv")
$py = Join-Path $InstallRoot ".venv\Scripts\python.exe"
$pip = Join-Path $InstallRoot ".venv\Scripts\pip.exe"
$pw = Join-Path $InstallRoot ".venv\Scripts\playwright.exe"
$fb = Join-Path $InstallRoot ".venv\Scripts\fb-poller.exe"
& $py -m pip install -U pip wheel | Out-Null
& $pip install -e "$InstallRoot[control]"
if ($LASTEXITCODE -ne 0) { & $pip install -e $InstallRoot }
& $pw install chromium

# 2) data + .env
Write-Host "[2/5] Tạo data/ và .env..."
New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "data"), (Join-Path $InstallRoot "logs"), (Join-Path $InstallRoot "data\metrics") | Out-Null
foreach ($f in @("posts.txt", "proxies_static.txt", "proxies_4g.txt")) {
  $p = Join-Path $InstallRoot "data\$f"
  if (-not (Test-Path $p)) { New-Item -ItemType File -Path $p | Out-Null }
}

$dbPath = ((Join-Path $InstallRoot "data\fb_poller.db") -replace "\\", "/")
@"
DATABASE_URL=sqlite+aiosqlite:///$dbPath
WORKER_ID=$workerId
ROLE=all
WORKERS=$Workers
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
PROXIES_STATIC_FILE=$InstallRoot\data\proxies_static.txt
PROXIES_4G_FILE=$InstallRoot\data\proxies_4g.txt
DATA_DIR=$InstallRoot\data
"@ | Set-Content -Path (Join-Path $InstallRoot ".env") -Encoding UTF8

& $fb init-db

# 3) agent config
Write-Host "[3/5] Ghi cấu hình agent..."
$cfg = Get-Content (Join-Path $AgentRoot "config.example.json") -Raw | ConvertFrom-Json
if ($ManifestUrl) { $cfg.manifest_url = $ManifestUrl }
if ($ControlUrl) {
  $cfg.control_url = $ControlUrl
  $cfg.prefer_lan = $true
  $cfg.sync_comments = $true
}
if ($ControlToken) { $cfg.control_token = $ControlToken }
$cfg.install_dir = $InstallRoot
$cfg.python_exe = $py
Save-Json -Object $cfg -Path (Join-Path $AgentRoot "config.json")

if (-not (Test-Path (Join-Path $AgentRoot "VERSION"))) {
  Set-Content (Join-Path $AgentRoot "VERSION") "1.0.0" -Encoding UTF8
}
if (-not (Test-Path (Join-Path $InstallRoot "VERSION"))) {
  Set-Content (Join-Path $InstallRoot "VERSION") "0.1.0" -Encoding UTF8
}

# 4) Scheduled Task — bật máy / đăng nhập là chạy agent
Write-Host "[4/5] Đăng ký tự chạy khi đăng nhập..."
$taskName = "FbPollerAgent"
$agentPs1 = Join-Path $AgentRoot "FbPollerAgent.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$agentPs1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

# 5) Optional start now
Write-Host "[5/5] Hoàn tất."
Write-Host ""
Write-Host "Machine ID : $machineId"
Write-Host "WORKER_ID  : $workerId"
Write-Host "WORKERS    : $Workers"
Write-Host ""
Write-Host "Bước tiếp theo:"
Write-Host "  1) Dán URL vào:   $InstallRoot\data\posts.txt"
Write-Host "  2) Dán proxy vào: $InstallRoot\data\proxies_static.txt"
Write-Host "  3) Import:"
Write-Host "       & `"$fb`" import-urls `"$InstallRoot\data\posts.txt`""
Write-Host "       & `"$fb`" import-proxies"
Write-Host "       & `"$fb`" rebalance-hot"
Write-Host "  4) Agent sẽ tự chạy ở lần đăng nhập tiếp theo."
Write-Host "     Chạy ngay:  powershell -ExecutionPolicy Bypass -File `"$agentPs1`" -Once"
Write-Host "     Hoặc:       Start-ScheduledTask -TaskName $taskName"

if ($StartNow) {
  Start-ScheduledTask -TaskName $taskName
  Write-Host "Đã Start-ScheduledTask $taskName"
}

Write-Host ""
Write-Host "Khuyến nghị: dùng 1 PC làm LAN server (băng thông cao, không nghẽn Internet):" -ForegroundColor Cyan
Write-Host "  xem docs\SERVER_PC.md  hoặc chạy Install-Server.ps1 trên máy chủ."
Write-Host "Agent sẽ ưu tiên tải update + sync comment qua control_url (LAN)."

