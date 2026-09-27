# Shared helpers for FbPoller Windows agent

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-AgentRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-InstallRoot {
  param([string]$AgentRoot = (Get-AgentRoot))
  # repo root = parent of pc_agent
  return (Resolve-Path (Join-Path $AgentRoot "..\..")).Path
}

function Get-StateDir {
  param([string]$AgentRoot = (Get-AgentRoot))
  $dir = Join-Path $AgentRoot "state"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  return $dir
}

function Get-AgentLogPath {
  param([string]$InstallRoot)
  $logDir = Join-Path $InstallRoot "logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null
  return (Join-Path $logDir "agent.log")
}

function Write-AgentLog {
  param(
    [string]$Message,
    [string]$Level = "INFO",
    [string]$InstallRoot = (Get-InstallRoot)
  )
  $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
  $path = Get-AgentLogPath -InstallRoot $InstallRoot
  Add-Content -Path $path -Value $line -Encoding UTF8
  Write-Host $line
}

function Get-OrCreateMachineId {
  param([string]$AgentRoot = (Get-AgentRoot))
  $state = Get-StateDir -AgentRoot $AgentRoot
  $file = Join-Path $state "machine_id.txt"
  if (Test-Path $file) {
    return (Get-Content $file -Raw).Trim()
  }
  $id = [guid]::NewGuid().ToString()
  Set-Content -Path $file -Value $id -Encoding UTF8
  return $id
}

function Read-AgentConfig {
  param([string]$AgentRoot = (Get-AgentRoot))
  $cfgPath = Join-Path $AgentRoot "config.json"
  if (-not (Test-Path $cfgPath)) {
    $example = Join-Path $AgentRoot "config.example.json"
    Copy-Item $example $cfgPath
  }
  return (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Save-Json {
  param($Object, [string]$Path)
  ($Object | ConvertTo-Json -Depth 8) | Set-Content -Path $Path -Encoding UTF8
}

function Get-LocalVersions {
  param([string]$InstallRoot, [string]$AgentRoot)
  $agentVerFile = Join-Path $AgentRoot "VERSION"
  $pollerVerFile = Join-Path $InstallRoot "VERSION"
  $agentVer = if (Test-Path $agentVerFile) { (Get-Content $agentVerFile -Raw).Trim() } else { "0.0.0" }
  $pollerVer = if (Test-Path $pollerVerFile) { (Get-Content $pollerVerFile -Raw).Trim() } else { "0.0.0" }
  return [pscustomobject]@{
    agent  = $agentVer
    poller = $pollerVer
  }
}

function Compare-Version {
  param([string]$A, [string]$B)
  $va = [version](($A -split "-")[0])
  $vb = [version](($B -split "-")[0])
  return $va.CompareTo($vb)
}

function Get-Sha256 {
  param([string]$Path)
  return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ControlHeaders {
  param($Config, [string]$MachineId = "")
  $h = @{}
  if ($MachineId) { $h["X-Machine-Id"] = $MachineId }
  if ($Config.control_token) {
    $h["Authorization"] = "Bearer $($Config.control_token)"
  }
  return $h
}

function Resolve-AbsoluteUrl {
  param([string]$Base, [string]$MaybeRelative)
  if (-not $MaybeRelative) { return $MaybeRelative }
  if ($MaybeRelative -match "^https?://") { return $MaybeRelative }
  if (-not $Base) { return $MaybeRelative }
  $b = $Base.TrimEnd("/")
  if ($MaybeRelative.StartsWith("/")) {
    return "$b$MaybeRelative"
  }
  return "$b/$MaybeRelative"
}

function Get-PreferredManifestUrl {
  param($Config)
  $preferLan = $true
  if ($null -ne $Config.PSObject.Properties["prefer_lan"]) {
    $preferLan = [bool]$Config.prefer_lan
  }
  if ($preferLan -and $Config.control_url) {
    return ($Config.control_url.TrimEnd("/")) + "/v1/updates/manifest"
  }
  return [string]$Config.manifest_url
}

function Invoke-JsonGet {
  param([string]$Url, [hashtable]$Headers = @{})
  return Invoke-RestMethod -Uri $Url -Method GET -Headers $Headers -TimeoutSec 120
}

function Invoke-JsonPost {
  param([string]$Url, $Body, [hashtable]$Headers = @{})
  $json = $Body | ConvertTo-Json -Depth 8 -Compress
  return Invoke-RestMethod -Uri $Url -Method POST -Headers $Headers -Body $json -ContentType "application/json; charset=utf-8" -TimeoutSec 120
}

function Get-LinkSpeedMbps {
  try {
    $adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue |
      Where-Object { $_.Status -eq "Up" -and $_.LinkSpeed }
    if (-not $adapters) { return $null }
    $best = $adapters | Sort-Object { [double]($_.LinkSpeed -replace "[^\d\.]", "") } -Descending | Select-Object -First 1
    $raw = [string]$best.LinkSpeed
    if ($raw -match "([\d\.]+)\s*Gbps") { return [double]$Matches[1] * 1000 }
    if ($raw -match "([\d\.]+)\s*Mbps") { return [double]$Matches[1] }
    return $null
  } catch {
    return $null
  }
}

function Invoke-ResumableDownload {
  <#
    High-bandwidth friendly download with Range resume.
    Uses large buffers; falls back to full download if server ignores Range.
  #>
  param(
    [string]$Url,
    [string]$OutFile,
    [hashtable]$Headers = @{},
    [int]$BufferBytes = 8MB
  )
  $tmp = "$OutFile.partial"
  $existing = 0L
  if (Test-Path $tmp) {
    $existing = (Get-Item $tmp).Length
  }

  $reqHeaders = @{}
  foreach ($k in $Headers.Keys) { $reqHeaders[$k] = $Headers[$k] }
  if ($existing -gt 0) {
    $reqHeaders["Range"] = "bytes=$existing-"
  }

  try {
    # Prefer HttpClient for streaming + resume
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(60)
    $msg = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $Url)
    foreach ($k in $reqHeaders.Keys) {
      [void]$msg.Headers.TryAddWithoutValidation($k, [string]$reqHeaders[$k])
    }
    $resp = $client.SendAsync($msg, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
    $code = [int]$resp.StatusCode
    if ($code -eq 416 -and $existing -gt 0) {
      # Already complete
      Move-Item -Path $tmp -Destination $OutFile -Force
      $client.Dispose()
      return
    }
    if (-not $resp.IsSuccessStatusCode) {
      throw "HTTP $code downloading $Url"
    }
    $append = ($code -eq 206)
    $mode = if ($append) { [System.IO.FileMode]::Append } else { [System.IO.FileMode]::Create }
    $fs = [System.IO.File]::Open($tmp, $mode, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
      $stream = $resp.Content.ReadAsStreamAsync().Result
      $buffer = New-Object byte[] $BufferBytes
      while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $fs.Write($buffer, 0, $read)
      }
      $stream.Dispose()
    } finally {
      $fs.Dispose()
      $client.Dispose()
    }
    Move-Item -Path $tmp -Destination $OutFile -Force
  } catch {
    # Fallback: full download via Invoke-WebRequest
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -Headers $Headers -UseBasicParsing -TimeoutSec 3600
  }
}
