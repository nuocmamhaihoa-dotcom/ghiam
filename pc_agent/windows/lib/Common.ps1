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

function Invoke-JsonGet {
  param([string]$Url, [hashtable]$Headers = @{})
  return Invoke-RestMethod -Uri $Url -Method GET -Headers $Headers -TimeoutSec 60
}

function Invoke-JsonPost {
  param([string]$Url, $Body, [hashtable]$Headers = @{})
  $json = $Body | ConvertTo-Json -Depth 6 -Compress
  return Invoke-RestMethod -Uri $Url -Method POST -Headers $Headers -Body $json -ContentType "application/json; charset=utf-8" -TimeoutSec 30
}
