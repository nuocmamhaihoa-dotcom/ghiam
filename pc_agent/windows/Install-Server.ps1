# Cài PC làm Server LAN băng thông cao:
# - FastAPI control plane (manifest + package + sync comments)
# - Tự chạy khi đăng nhập
# - Nhận agent từ mọi PC trong mạng
[CmdletBinding()]
param(
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 8088,
  [string]$Token = "",
  [int]$MaxUploadMb = 512,
  [switch]$StartNow
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\Common.ps1")
$AgentRoot = Get-AgentRoot
$InstallRoot = Get-InstallRoot

Write-Host "=== Cài FbPoller LAN Server (Windows) ===" -ForegroundColor Green
Write-Host "InstallRoot = $InstallRoot"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Cần Python 3.11+ trong PATH."
}

Write-Host "[1/4] Cài venv + control plane deps..."
Set-Location $InstallRoot
if (-not (Test-Path (Join-Path $InstallRoot ".venv"))) {
  python -m venv (Join-Path $InstallRoot ".venv")
}
$py = Join-Path $InstallRoot ".venv\Scripts\python.exe"
$pip = Join-Path $InstallRoot ".venv\Scripts\pip.exe"
& $py -m pip install -U pip wheel | Out-Null
& $pip install -e "$InstallRoot[control]"

Write-Host "[2/4] Tạo thư mục packages + token..."
$dataDir = Join-Path $InstallRoot "control_data"
$pkgDir = Join-Path $dataDir "packages"
New-Item -ItemType Directory -Force -Path $dataDir, $pkgDir, (Join-Path $InstallRoot "logs") | Out-Null

if (-not $Token) {
  $Token = [guid]::NewGuid().ToString("N").Substring(0, 24)
}
$envFile = Join-Path $InstallRoot "control_data\server.env"
@"
CONTROL_HOST=$BindHost
CONTROL_PORT=$Port
CONTROL_TOKEN=$Token
CONTROL_DATA_DIR=$dataDir
CONTROL_PACKAGES_DIR=$pkgDir
CONTROL_MAX_UPLOAD_MB=$MaxUploadMb
CONTROL_UVICORN_WORKERS=2
CONTROL_LIMIT_CONCURRENCY=200
CONTROL_BACKLOG=2048
CONTROL_KEEPALIVE=75
"@ | Set-Content -Path $envFile -Encoding UTF8

# Helper launcher that loads env then starts server
$launcher = Join-Path $AgentRoot "Start-Server.ps1"
@"
# Auto-generated — start high-bandwidth LAN server
`$ErrorActionPreference = 'Stop'
`$InstallRoot = '$InstallRoot'
`$envFile = Join-Path `$InstallRoot 'control_data\server.env'
Get-Content `$envFile | ForEach-Object {
  if (`$_ -match '^\s*#' -or `$_ -notmatch '=') { return }
  `$k, `$v = `$_.Split('=', 2)
  Set-Item -Path "Env:`$k" -Value `$v
}
`$logDir = Join-Path `$InstallRoot 'logs'
New-Item -ItemType Directory -Force -Path `$logDir | Out-Null
`$py = Join-Path `$InstallRoot '.venv\Scripts\python.exe'
`$out = Join-Path `$logDir 'server.out'
`$err = Join-Path `$logDir 'server.err'
`$p = Start-Process -FilePath `$py -ArgumentList '-m','control_plane' -WorkingDirectory `$InstallRoot ``
  -RedirectStandardOutput `$out -RedirectStandardError `$err -WindowStyle Hidden -PassThru
Set-Content (Join-Path `$logDir 'server.pid') `$p.Id -Encoding UTF8
Write-Host "LAN server started pid=`$(`$p.Id) http://0.0.0.0:`$env:CONTROL_PORT/"
"@ | Set-Content -Path $launcher -Encoding UTF8

Write-Host "[3/4] Đăng ký Task Scheduler FbPollerServer..."
$taskName = "FbPollerServer"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

# Guess LAN IP for printout
$lanIp = "IP_MAY_NAY"
try {
  $lanIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown"
  } | Select-Object -First 1).IPAddress
} catch {}

Write-Host "[4/4] Hoàn tất."
Write-Host ""
Write-Host "Server URL : http://${lanIp}:$Port/" -ForegroundColor Cyan
Write-Host "Token      : $Token" -ForegroundColor Cyan
Write-Host "Packages   : $pkgDir"
Write-Host ""
Write-Host "Đặt file zip release vào packages\, hoặc upload:"
Write-Host "  curl -H `"Authorization: Bearer $Token`" -F file=@fb-poller.zip http://${lanIp}:$Port/v1/updates/packages/upload"
Write-Host ""
Write-Host "Trên PC scanner cài agent trỏ về server này:"
Write-Host "  Install-Agent.ps1 -ControlUrl `"http://${lanIp}:$Port`" -ControlToken `"$Token`" -StartNow"
Write-Host ""
Write-Host "Firewall: mở TCP $Port inbound (Private network)."

if ($StartNow) {
  Start-ScheduledTask -TaskName $taskName
  Write-Host "Đã Start-ScheduledTask $taskName"
}
