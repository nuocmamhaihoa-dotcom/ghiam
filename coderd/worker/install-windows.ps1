$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  py -3.12 -m venv (Join-Path $Root ".venv")
  $Python = Join-Path $Root ".venv\Scripts\python.exe"
  & $Python -m pip install -r (Join-Path $Root "worker\requirements.txt")
}
$EnvFile = Join-Path $Root "worker.env"
if (-not (Test-Path $EnvFile)) {
  @"
CODERD_URL=http://127.0.0.1:8080
WORKER_TOKEN=
WORKSPACE_ROOT=$env:USERPROFILE\coderd-workspace
"@ | Set-Content -Path $EnvFile -Encoding ascii
}
$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m worker.connect" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "coderd-worker" -Action $Action -Trigger $Trigger -Force | Out-Null
Write-Output "coderd-worker"
