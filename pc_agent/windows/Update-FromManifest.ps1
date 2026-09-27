# Tải và áp dụng bản cập nhật từ manifest (giữ data/.env/logs/state)
# Ưu tiên LAN server khi prefer_lan=true + control_url — băng thông cao, resume.
[CmdletBinding()]
param(
  [string]$ManifestUrl,
  [switch]$Force
)

. (Join-Path $PSScriptRoot "lib\Common.ps1")

$AgentRoot = Get-AgentRoot
$InstallRoot = Get-InstallRoot
$Config = Read-AgentConfig -AgentRoot $AgentRoot
$MachineId = Get-OrCreateMachineId -AgentRoot $AgentRoot
$Headers = Get-ControlHeaders -Config $Config -MachineId $MachineId

if (-not $ManifestUrl) {
  $ManifestUrl = Get-PreferredManifestUrl -Config $Config
}

Write-AgentLog "Checking updates: $ManifestUrl" -InstallRoot $InstallRoot
try {
  $manifest = Invoke-JsonGet -Url $ManifestUrl -Headers $Headers
} catch {
  # Fallback Internet manifest if LAN fails
  if ($Config.manifest_url -and $ManifestUrl -ne $Config.manifest_url) {
    Write-AgentLog "LAN manifest failed, fallback Internet: $($_.Exception.Message)" -Level "WARN" -InstallRoot $InstallRoot
    $ManifestUrl = [string]$Config.manifest_url
    $manifest = Invoke-JsonGet -Url $ManifestUrl
  } else {
    throw
  }
}

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

$pkgUrlRaw = [string]$manifest.agent.package_url
if (-not $pkgUrlRaw) { throw "manifest.agent.package_url is empty" }

# Resolve relative LAN paths (/v1/updates/packages/...) against control_url or manifest origin
$baseForResolve = $null
if ($Config.control_url) { $baseForResolve = [string]$Config.control_url }
elseif ($ManifestUrl -match "^(https?://[^/]+)") { $baseForResolve = $Matches[1] }
$pkgUrl = Resolve-AbsoluteUrl -Base $baseForResolve -MaybeRelative $pkgUrlRaw

$tmp = Join-Path $env:TEMP ("fb-poller-update-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zip = Join-Path $tmp "package.zip"
$extract = Join-Path $tmp "extract"

Write-AgentLog "Downloading (LAN/resume): $pkgUrl" -InstallRoot $InstallRoot
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Invoke-ResumableDownload -Url $pkgUrl -OutFile $zip -Headers $Headers
$sw.Stop()
$bytes = (Get-Item $zip).Length
$mbps = if ($sw.Elapsed.TotalSeconds -gt 0) { [math]::Round(($bytes * 8 / 1e6) / $sw.Elapsed.TotalSeconds, 1) } else { 0 }
Write-AgentLog ("Downloaded {0:N0} bytes in {1}ms (~{2} Mbps)" -f $bytes, $sw.ElapsedMilliseconds, $mbps) -InstallRoot $InstallRoot

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
  "control_data",
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
robocopy $sourceRoot $InstallRoot /E /XD .git .venv data logs control_data pc_agent\windows\state /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null

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
  & $venvPip install -e "$InstallRoot[control]" 2>$null
  if ($LASTEXITCODE -ne 0) {
    & $venvPip install -e $InstallRoot | Out-Null
  }
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
  transport      = [string]$manifest.transport
  download_mbps  = $mbps
} -Path (Join-Path $stateDir "last_update.json")

Write-AgentLog "Update applied agent=$remoteAgent poller=$remotePoller transport=$([string]$manifest.transport)" -InstallRoot $InstallRoot
return [pscustomobject]@{ updated = $true; agent = $remoteAgent; poller = $remotePoller; mbps = $mbps }
