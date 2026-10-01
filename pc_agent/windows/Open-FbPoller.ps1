# Mo file la chay. Khong hoi token. Cua so nay giu ket noi voi hub.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { chcp 65001 > $null } catch { }

$Root = Join-Path $env:LOCALAPPDATA "FbPollerVideo"
New-Item -ItemType Directory -Force -Path $Root, (Join-Path $Root "logs") | Out-Null

$mutex = $null
$owned = $false
try {
  $mutex = New-Object System.Threading.Mutex($false, "Local\FbPollerVideo")
} catch {
  $mutex = $null
}
if ($mutex) {
  try {
    $owned = $mutex.WaitOne(0)
  } catch [System.Threading.AbandonedMutexException] {
    $owned = $true
  }
  if (-not $owned) {
    Write-Host "FbPoller dang chay roi. Cua so kia van noi hub."
    exit 0
  }
}

try {
  $hub = if ($env:CONTROL_HUB) { $env:CONTROL_HUB.TrimEnd("/") } else { "http://222.255.214.202:8088" }
  $token = [string]$env:CONTROL_TOKEN
  $cfgPath = Join-Path $Root "config.json"
  if ($token) {
    @{ hub = $hub; token = $token } | ConvertTo-Json | Set-Content -Encoding utf8 $cfgPath
  } elseif (Test-Path $cfgPath) {
    $existing = Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json
    if ($existing.hub) { $hub = ([string]$existing.hub).TrimEnd("/") }
    $env:CONTROL_HUB = $hub
    if ($existing.token) { $env:CONTROL_TOKEN = [string]$existing.token }
  } else {
    throw "Thieu token. Tai lai FbPoller.bat tu trang hub."
  }

  Write-Host "Dang chuan bi. Lan dau co the mat vai phut."
  $installer = Join-Path $Root "Install-VideoWorker.ps1"
  Invoke-WebRequest -UseBasicParsing -Uri "$hub/cai-video.ps1" -OutFile $installer
  & $installer

  $startup = [Environment]::GetFolderPath("Startup")
  if ($startup) {
    @(
      "@echo off",
      "chcp 65001 >nul",
      "title FbPoller",
      ":again",
      "powershell -NoProfile -ExecutionPolicy Bypass -File `"%LOCALAPPDATA%\FbPollerVideo\Open-FbPoller.ps1`"",
      "if errorlevel 1 (",
      "  timeout /t 5 /nobreak >nul",
      "  goto again",
      ")",
      "exit /b 0"
    ) -join "`r`n" | Set-Content -Encoding ascii (Join-Path $startup "FbPoller.bat")
  }

  Write-Host "Dang noi hub. De cua so nay mo."
  & (Join-Path $Root "Run-VideoWorker.ps1")
  $code = 1
  if ($null -ne $global:FB_WORKER_EXIT) { $code = [int]$global:FB_WORKER_EXIT }
  exit $code
} finally {
  if ($owned -and $mutex) {
    try { [void]$mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
  }
}
