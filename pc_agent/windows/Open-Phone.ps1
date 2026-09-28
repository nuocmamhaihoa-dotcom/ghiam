# Mở giả lập điện thoại của hub trên PC này.
# Địa chỉ lấy từ config.json (control_url) do Install-Agent.ps1 ghi.
[CmdletBinding()]
param(
  [string]$ControlUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\Common.ps1")

if (-not $ControlUrl) {
  $cfg = Read-AgentConfig
  if ($cfg.PSObject.Properties.Name -contains "control_url") {
    $ControlUrl = [string]$cfg.control_url
  }
}
$ControlUrl = $ControlUrl.Trim().TrimEnd("/")
if (-not $ControlUrl) {
  throw "Chưa có control_url. Chạy Install-Agent.ps1 -ControlUrl http://địa-chỉ-hub:8088 -ControlToken ..."
}

$phone = "$ControlUrl/phone"
Start-Process $phone
Write-Host "Đã mở giả lập điện thoại: $phone"
