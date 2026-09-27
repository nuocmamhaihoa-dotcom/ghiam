# Agent Windows: tự kết nối, tự cập nhật, giữ poller chạy
# Chạy bởi Scheduled Task lúc đăng nhập / khi bật máy.
[CmdletBinding()]
param(
  [switch]$Once
)

. (Join-Path $PSScriptRoot "lib\Common.ps1")

$AgentRoot = Get-AgentRoot
$InstallRoot = Get-InstallRoot
$Config = Read-AgentConfig -AgentRoot $AgentRoot
$MachineId = Get-OrCreateMachineId -AgentRoot $AgentRoot
$StateDir = Get-StateDir -AgentRoot $AgentRoot
$Versions = Get-LocalVersions -InstallRoot $InstallRoot -AgentRoot $AgentRoot

Write-AgentLog "Agent start machine_id=$MachineId agent=$($Versions.agent) poller=$($Versions.poller)" -InstallRoot $InstallRoot

function Get-ControlHeaders {
  $h = @{ "X-Machine-Id" = $MachineId }
  if ($Config.control_token) {
    $h["Authorization"] = "Bearer $($Config.control_token)"
  }
  return $h
}

function Connect-ControlPlane {
  if (-not $Config.control_url) {
    Write-AgentLog "No control_url — offline mode (update-only)" -InstallRoot $InstallRoot
    return
  }
  $body = @{
    machine_id   = $MachineId
    hostname     = $env:COMPUTERNAME
    agent_version  = $Versions.agent
    poller_version = $Versions.poller
    os           = "windows"
    channel      = $Config.channel
    started_at   = (Get-Date).ToString("o")
  }
  try {
    $url = ($Config.control_url.TrimEnd("/")) + "/v1/agents/register"
    Invoke-JsonPost -Url $url -Body $body -Headers (Get-ControlHeaders) | Out-Null
    Write-AgentLog "Connected/registered to control plane" -InstallRoot $InstallRoot
    Set-Content -Path (Join-Path $StateDir "connected.txt") -Value (Get-Date).ToString("o") -Encoding UTF8
  } catch {
    Write-AgentLog "Register failed: $($_.Exception.Message)" -Level "WARN" -InstallRoot $InstallRoot
  }
}

function Send-Heartbeat {
  if (-not $Config.control_url) { return }
  $pollerRunning = Test-PollerRunning
  $body = @{
    machine_id     = $MachineId
    hostname       = $env:COMPUTERNAME
    agent_version  = (Get-LocalVersions -InstallRoot $InstallRoot -AgentRoot $AgentRoot).agent
    poller_version = (Get-LocalVersions -InstallRoot $InstallRoot -AgentRoot $AgentRoot).poller
    poller_running = $pollerRunning
    ts             = (Get-Date).ToString("o")
  }
  try {
    $url = ($Config.control_url.TrimEnd("/")) + "/v1/agents/heartbeat"
    Invoke-JsonPost -Url $url -Body $body -Headers (Get-ControlHeaders) | Out-Null
  } catch {
    Write-AgentLog "Heartbeat failed: $($_.Exception.Message)" -Level "WARN" -InstallRoot $InstallRoot
  }
}

function Test-PollerRunning {
  $pidFile = Join-Path $InstallRoot "logs\fb-poller.pid"
  if (-not (Test-Path $pidFile)) { return $false }
  $procId = (Get-Content $pidFile -Raw).Trim()
  try {
    Get-Process -Id ([int]$procId) -ErrorAction Stop | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Start-PollerProcess {
  $fb = Join-Path $InstallRoot ".venv\Scripts\fb-poller.exe"
  if (-not (Test-Path $fb)) {
    Write-AgentLog "fb-poller.exe missing — run Install-Agent.ps1 first" -Level "ERROR" -InstallRoot $InstallRoot
    return
  }
  if (Test-PollerRunning) {
    Write-AgentLog "Poller already running" -InstallRoot $InstallRoot
    return
  }
  $logDir = Join-Path $InstallRoot "logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  $out = Join-Path $logDir "fb-poller.out"
  $p = Start-Process -FilePath $fb -ArgumentList "run" -WorkingDirectory $InstallRoot `
    -RedirectStandardOutput $out -RedirectStandardError $out `
    -WindowStyle Hidden -PassThru
  Set-Content -Path (Join-Path $logDir "fb-poller.pid") -Value $p.Id -Encoding UTF8
  Write-AgentLog "Started poller pid=$($p.Id)" -InstallRoot $InstallRoot
}

function Invoke-UpdateCycle {
  if (-not $Config.auto_update) { return }
  try {
    $result = & (Join-Path $AgentRoot "Update-FromManifest.ps1") -ManifestUrl ([string]$Config.manifest_url)
    if ($result -and $result.updated) {
      Write-AgentLog "Update applied — restarting poller" -InstallRoot $InstallRoot
      Start-PollerProcess
    }
  } catch {
    Write-AgentLog "Update error: $($_.Exception.Message)" -Level "WARN" -InstallRoot $InstallRoot
  }
}

# --- main ---
Connect-ControlPlane
Invoke-UpdateCycle
if ($Config.ensure_poller_running) { Start-PollerProcess }
Send-Heartbeat

if ($Once) {
  Write-AgentLog "Once mode done" -InstallRoot $InstallRoot
  exit 0
}

$lastUpdate = Get-Date
$lastBeat = Get-Date
$updateEvery = [TimeSpan]::FromMinutes([math]::Max(5, [int]$Config.update_check_minutes))
$beatEvery = [TimeSpan]::FromMinutes([math]::Max(1, [int]$Config.heartbeat_minutes))

Write-AgentLog "Agent loop running (update every $($updateEvery.TotalMinutes)m, heartbeat every $($beatEvery.TotalMinutes)m)" -InstallRoot $InstallRoot

while ($true) {
  Start-Sleep -Seconds 20
  try {
    if ($Config.ensure_poller_running -and $Config.poller_restart_on_exit -and -not (Test-PollerRunning)) {
      Write-AgentLog "Poller not running — restarting" -Level "WARN" -InstallRoot $InstallRoot
      Start-PollerProcess
    }
    if ((Get-Date) - $lastUpdate -ge $updateEvery) {
      Invoke-UpdateCycle
      $lastUpdate = Get-Date
    }
    if ((Get-Date) - $lastBeat -ge $beatEvery) {
      Send-Heartbeat
      $lastBeat = Get-Date
    }
  } catch {
    Write-AgentLog "Loop error: $($_.Exception.Message)" -Level "ERROR" -InstallRoot $InstallRoot
  }
}
