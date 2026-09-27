# Gỡ agent (không xóa data/ comment đã quét)
[CmdletBinding()]
param([switch]$RemoveFiles)

. (Join-Path $PSScriptRoot "lib\Common.ps1")
$InstallRoot = Get-InstallRoot
$taskName = "FbPollerAgent"

Write-Host "Stopping scheduled task..."
try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

$pidFile = Join-Path $InstallRoot "logs\fb-poller.pid"
if (Test-Path $pidFile) {
  $procId = (Get-Content $pidFile -Raw).Trim()
  try { Stop-Process -Id ([int]$procId) -Force -ErrorAction SilentlyContinue } catch {}
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Also stop agent powershell if any
Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*FbPollerAgent.ps1*" } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {} }

if ($RemoveFiles) {
  Write-Host "RemoveFiles không xóa toàn bộ repo (an toàn). Chỉ gỡ task + dừng process."
}

Write-Host "Đã gỡ agent. Dữ liệu data/ vẫn còn."
