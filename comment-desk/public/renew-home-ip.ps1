# Chạy trên PC nhà. Khi VPS báo IP cháy, script gọi lệnh trong renew-cmd.txt
# (một dòng, do bạn tự ghi, ví dụ lệnh khởi động lại modem), rồi so IP công cộng.
$ErrorActionPreference = "Continue"
$Base = if ($env:PC_BASE) { $env:PC_BASE } else { "http://14.225.198.188:8088" }
$Token = $env:PC_TOKEN
$HostName = if ($env:PC_HOST) { $env:PC_HOST } else { $env:COMPUTERNAME }
$CmdFile = Join-Path $PSScriptRoot "renew-cmd.txt"

function Get-PublicIp {
  try {
    return (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 20).ToString().Trim()
  } catch {
    return ""
  }
}

while ($true) {
  try {
    $uri = "$Base/api/pc/command?token=$([uri]::EscapeDataString($Token))&host=$([uri]::EscapeDataString($HostName))"
    $cmd = Invoke-RestMethod -Uri $uri -TimeoutSec 20
    if ($cmd.action -eq "renew") {
      $old = Get-PublicIp
      $started = $false
      if (Test-Path $CmdFile) {
        $line = @(Get-Content -Path $CmdFile -TotalCount 1)[0]
        if ($line -and $line.Trim()) {
          cmd /c $line.Trim()
          $started = $true
          Start-Sleep -Seconds 90
        }
      }
      $new = Get-PublicIp
      $payload = @{ token = $Token; host = $HostName; oldIp = $old; newIp = $new; started = $started } | ConvertTo-Json
      Invoke-RestMethod -Method Post -Uri "$Base/api/pc/renew" -ContentType "application/json; charset=utf-8" -Body $payload | Out-Null
    }
  } catch {
    Write-Output $_.Exception.Message
  }
  Start-Sleep -Seconds 20
}
