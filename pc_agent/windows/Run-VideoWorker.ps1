# Chạy worker đọc video. Token và hub lấy từ config.json cạnh file này.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Get-Content -Raw -Encoding UTF8 (Join-Path $Root "config.json") | ConvertFrom-Json
if (-not $cfg.token) { throw "Thieu token trong config.json" }
$env:CONTROL_TOKEN = [string]$cfg.token
$env:CONTROL_HUB = [string]$cfg.hub
$env:FB_VIDEO_STATE = Join-Path $Root "state.json"
$tessdata = Join-Path $Root "tessdata"
if (-not $tessdata.EndsWith("\")) { $tessdata = $tessdata + "\" }
$env:TESSDATA_PREFIX = $tessdata
$env:OMP_THREAD_LIMIT = "1"

$dirs = New-Object System.Collections.Generic.List[string]
foreach ($dir in @(
  "C:\Program Files\Tesseract-OCR",
  "C:\Program Files (x86)\Tesseract-OCR",
  (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
)) {
  if ($dir -and (Test-Path $dir)) { $dirs.Add($dir) }
}
$ffmpegRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
if (Test-Path $ffmpegRoot) {
  $ffmpeg = Get-ChildItem -Path $ffmpegRoot -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($ffmpeg) { $dirs.Add($ffmpeg.DirectoryName) }
}
if ($dirs.Count -gt 0) {
  $env:Path = (($dirs | Select-Object -Unique) -join ";") + ";" + $env:Path
}

$py = Join-Path $Root "py\Scripts\python.exe"
$code = Join-Path $Root "current"
if (-not (Test-Path $py)) { throw "Chua co Python venv" }
$worker = Join-Path $code "pc_agent\video_worker.py"
if (-not (Test-Path $worker)) { throw "Chua co ma doc video" }
Set-Location $code
$log = Join-Path $Root "logs\worker.log"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
& $py $worker --hub $env:CONTROL_HUB *>> $log
exit $LASTEXITCODE
