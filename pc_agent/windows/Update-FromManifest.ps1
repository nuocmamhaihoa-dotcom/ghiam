# Tải và áp dụng bản cập nhật từ manifest (giữ data/.env/logs/state)
[CmdletBinding()]
param(
  [string]$ManifestUrl,
  [switch]$Force
)

. (Join-Path $PSScriptRoot "lib\Common.ps1")

$AgentRoot = Get-AgentRoot
$InstallRoot = Get-InstallRoot
$Config = Read-AgentConfig -AgentRoot $AgentRoot
if (-not $ManifestUrl) { $ManifestUrl = [string]$Config.manifest_url }

Write-AgentLog "Checking updates: $ManifestUrl" -InstallRoot $InstallRoot
$manifest = Invoke-JsonGet -Url $ManifestUrl
$local = Get-LocalVersions -InstallRoot $InstallRoot -AgentRoot $AgentRoot
$remoteAgent = [string]$manifest.agent.version
$remotePoller = [string]$manifest.poller.version

$need =
  $Force -or
  ((Compare-Version $remoteAgent $local.agent) -gt 0) -or
  ((Compare-Version $remotePoller $local.poller) -gt 0)

if (-not $need) {
  Write-AgentLog "Already up to date (agent=$($local.agent) poller=$($local.poller))" -InstallRoot $InstallRoot
  return [pscustomobject]@{ updated = $false; agent = $local.agent; poller = $local.poller }
}

$pkgUrl = [string]$manifest.agent.package_url
if (-not $pkgUrl) { throw "manifest.agent.package_url is empty" }

$tmp = Join-Path $env:TEMP ("fb-poller-update-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "package.zip"
$extract = Join-Path $tmp "extract"

Write-AgentLog "Downloading $pkgUrl" -InstallRoot $InstallRoot
Invoke-WebRequest -Uri $pkgUrl -OutFile $zip -UseBasicParsing

$expected = ([string]$manifest.agent.sha256).ToLowerInvariant()
if ($expected -and $expected -ne "REPLACE_WITH_SHA256_OF_ZIP") {
  $actual = Get-Sha256 -Path $zip
  if ($actual -ne $expected) {
    throw "SHA256 mismatch. expected=$expected actual=$actual"
  }
}

Expand-Archive -Path $zip -DestinationPath $extract -Force

# Zip may contain a single top-level folder
$sourceRoot = $extract
$children = Get-ChildItem $extract
if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
  $sourceRoot = $children[0].FullName
}

# Preserve local runtime data
$preserve = @(
  "data",
  "logs",
  ".env",
  "pc_agent\windows\config.json",
  "pc_agent\windows\state",
  "deploy\env.pc12",
  "deploy\env.pc20"
)

$backup = Join-Path $tmp "preserve"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($rel in $preserve) {
  $src = Join-Path $InstallRoot $rel
  if (Test-Path $src) {
    $dst = Join-Path $backup $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item -Path $src -Destination $dst -Recurse -Force
  }
}

# Stop poller if running via pid file
$pidFile = Join-Path $InstallRoot "logs\fb-poller.pid"
if (Test-Path $pidFile) {
  $pollerPid = (Get-Content $pidFile -Raw).Trim()
  try {
    Stop-Process -Id ([int]$pollerPid) -Force -ErrorAction SilentlyContinue
  } catch {}
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-AgentLog "Applying package files..." -InstallRoot $InstallRoot
# Copy new tree over install root (exclude preserved paths from wipe)
robocopy $sourceRoot $InstallRoot /E /XD .git .venv data logs pc_agent\windows\state /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null

# Restore preserved
foreach ($rel in $preserve) {
  $src = Join-Path $backup $rel
  if (Test-Path $src) {
    $dst = Join-Path $InstallRoot $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
    Copy-Item -Path $src -Destination $dst -Recurse -Force
  }
}

# Refresh Python package if venv exists
$venvPy = Join-Path $InstallRoot ".venv\Scripts\python.exe"
$venvPip = Join-Path $InstallRoot ".venv\Scripts\pip.exe"
if (Test-Path $venvPy) {
  Write-AgentLog "Updating Python package in venv..." -InstallRoot $InstallRoot
  & $venvPip install -e $InstallRoot | Out-Null
}

# Write versions
Set-Content -Path (Join-Path $AgentRoot "VERSION") -Value $remoteAgent -Encoding UTF8
Set-Content -Path (Join-Path $InstallRoot "VERSION") -Value $remotePoller -Encoding UTF8

$stateDir = Get-StateDir -AgentRoot $AgentRoot
Save-Json -Object @{
  last_update_at = (Get-Date).ToString("o")
  agent          = $remoteAgent
  poller         = $remotePoller
  package_url    = $pkgUrl
} -Path (Join-Path $stateDir "last_update.json")

Write-AgentLog "Update applied agent=$remoteAgent poller=$remotePoller" -InstallRoot $InstallRoot
return [pscustomobject]@{ updated = $true; agent = $remoteAgent; poller = $remotePoller }
