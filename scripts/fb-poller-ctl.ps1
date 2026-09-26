# Điều khiển fb-poller trên Windows
param(
  [Parameter(Position = 0)]
  [ValidateSet("start", "stop", "restart", "status", "import", "probe", "help")]
  [string]$Command = "help",
  [int]$Limit = 10,
  [int]$Workers = 2
)

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$PidFile = Join-Path $Root "logs\fb-poller.pid"
$LogFile = Join-Path $Root "logs\fb-poller.out"
$Fb = Join-Path $Root ".venv\Scripts\fb-poller.exe"

function Ensure-Installed {
  if (-not (Test-Path $Fb)) {
    Write-Host "Chưa cài. Chạy: .\install.ps1" -ForegroundColor Red
    exit 1
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
}

function Is-Running {
  if (-not (Test-Path $PidFile)) { return $false }
  $procId = Get-Content $PidFile
  try {
    Get-Process -Id $procId -ErrorAction Stop | Out-Null
    return $true
  } catch { return $false }
}

switch ($Command) {
  "start" {
    Ensure-Installed
    if (Is-Running) { Write-Host "Đang chạy pid=$(Get-Content $PidFile)"; break }
    $p = Start-Process -FilePath $Fb -ArgumentList "run" -WorkingDirectory $Root `
      -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile `
      -WindowStyle Hidden -PassThru
    Set-Content -Path $PidFile -Value $p.Id
    Write-Host "Started pid=$($p.Id) log=$LogFile"
  }
  "stop" {
    if (-not (Is-Running)) { Write-Host "Không chạy"; Remove-Item $PidFile -ErrorAction SilentlyContinue; break }
    $procId = Get-Content $PidFile
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Write-Host "Stopped"
  }
  "restart" {
    & $PSCommandPath stop
    & $PSCommandPath start
  }
  "status" {
    Ensure-Installed
    if (Is-Running) { Write-Host "running pid=$(Get-Content $PidFile)" } else { Write-Host "stopped" }
    & $Fb status
  }
  "import" {
    Ensure-Installed
    & $Fb import-urls (Join-Path $Root "data\posts.txt")
    & $Fb import-proxies
    & $Fb rebalance-hot
    & $Fb status
  }
  "probe" {
    Ensure-Installed
    & $Fb probe --limit $Limit --workers $Workers
  }
  default {
    Write-Host @"
fb-poller-ctl.ps1 start|stop|restart|status|import|probe
"@
  }
}
