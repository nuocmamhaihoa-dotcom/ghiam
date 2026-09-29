# Một lệnh trên Windows. Không cài máy quét comment.
# Không cài thư viện GPU. Card NVIDIA vẫn đọc bằng CPU cho đến khi tự cài sau.
#
# powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:CONTROL_TOKEN='TOKEN'; irm http://222.255.214.202:8088/cai-video.ps1 | iex"
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Hub = if ($env:CONTROL_HUB) { $env:CONTROL_HUB.TrimEnd("/") } else { "http://222.255.214.202:8088" }
$Root = Join-Path $env:LOCALAPPDATA "FbPollerVideo"
New-Item -ItemType Directory -Force -Path $Root, (Join-Path $Root "logs"), (Join-Path $Root "tessdata") | Out-Null

function Update-SessionPath {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
}

function Find-PythonExe {
  Update-SessionPath
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
  $root = Join-Path $env:LOCALAPPDATA "Programs\Python"
  if (Test-Path $root) {
    $found = Get-ChildItem $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match "Python3" } |
      Select-Object -First 1
    if ($found) { return $found.FullName }
  }
  return $null
}

function Find-TesseractExe {
  foreach ($path in @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
  )) {
    if (Test-Path $path) { return $path }
  }
  $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

function Install-Winget {
  param([string]$Id, [string]$Scope)
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "May chua co winget. Cai App Installer tu Microsoft Store roi chay lai lenh."
  }
  & winget install --id $Id -e --scope $Scope --accept-package-agreements --accept-source-agreements --disable-interactivity
  Update-SessionPath
}

$cfgPath = Join-Path $Root "config.json"
$token = [string]$env:CONTROL_TOKEN
if (-not $token -and (Test-Path $cfgPath)) {
  $existing = Get-Content -Raw -Encoding UTF8 $cfgPath | ConvertFrom-Json
  $token = [string]$existing.token
  if (-not $env:CONTROL_HUB -and $existing.hub) { $Hub = ([string]$existing.hub).TrimEnd("/") }
}
if (-not $token) { throw "Dat CONTROL_TOKEN bang token cua hub roi chay lai lenh." }

Write-Host "Cai cong cu doc video vao $Root"
Write-Host "Hub $Hub"

if (-not (Find-PythonExe)) {
  Write-Host "Cai Python"
  Install-Winget -Id "Python.Python.3.12" -Scope "user"
}
$python = Find-PythonExe
if (-not $python) { throw "Chua cai duoc Python." }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Cai ffmpeg"
  Install-Winget -Id "Gyan.FFmpeg" -Scope "user"
}
Update-SessionPath
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "Chua cai duoc ffmpeg." }

if (-not (Find-TesseractExe)) {
  Write-Host "Cai Tesseract"
  Install-Winget -Id "UB-Mannheim.TesseractOCR" -Scope "machine"
}
if (-not (Find-TesseractExe)) {
  Install-Winget -Id "UB-Mannheim.TesseractOCR" -Scope "user"
}
if (-not (Find-TesseractExe)) { throw "Chua cai duoc Tesseract. Chay lai cua so PowerShell bang quyen quan tri." }

function Save-TrainedData {
  param([string]$Name, [int]$MinBytes)
  $dest = Join-Path $Root "tessdata\$Name.traineddata"
  if ((Test-Path $dest) -and ((Get-Item $dest).Length -ge $MinBytes)) { return }
  $url = "https://github.com/tesseract-ocr/tessdata/raw/main/$Name.traineddata"
  Write-Host "Tai $Name"
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest
  if ((Get-Item $dest).Length -lt $MinBytes) { throw "File $Name tai ve bi thieu." }
}
Save-TrainedData -Name "eng" -MinBytes 1000000
Save-TrainedData -Name "vie" -MinBytes 100000

@{ hub = $Hub; token = $token } | ConvertTo-Json | Set-Content -Encoding utf8 $cfgPath

function Save-HubFile {
  param([string]$Url, [string]$Dest, [string]$Marker)
  Write-Host "Tai $Url"
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Dest
  $body = Get-Content -Raw -Encoding UTF8 $Dest
  if ($body -notlike "*$Marker*") { throw "Noi dung tai ve khong dung: $Url" }
}
Save-HubFile -Url "$Hub/cai-video-run.ps1" -Dest (Join-Path $Root "Run-VideoWorker.ps1") -Marker "FB_VIDEO_STATE"
Save-HubFile -Url "$Hub/cai-video-watchdog.py" -Dest (Join-Path $Root "video_watchdog.py") -Marker "upgrade_allowed"

$venvPy = Join-Path $Root "py\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "Tao moi truong Python"
  & $python -m venv (Join-Path $Root "py")
}
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install pillow
if ($LASTEXITCODE -ne 0) { throw "Chua cai duoc pillow." }

schtasks /End /TN FbPollerVideoWorker | Out-Null
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like "*video_worker.py*" } | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "Tai ma doc video"
& $venvPy (Join-Path $Root "video_watchdog.py") --install --root $Root
if ($LASTEXITCODE -ne 0) { throw "Chua tai duoc ma doc video." }

@'
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $Root "py\Scripts\python.exe"
& $py (Join-Path $Root "video_watchdog.py")
exit $LASTEXITCODE
'@ | Set-Content -Encoding utf8 (Join-Path $Root "Update-VideoWorker.ps1")

$runner = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Root\Run-VideoWorker.ps1`""
$updater = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Root\Update-VideoWorker.ps1`""
schtasks /Create /TN FbPollerVideoWorker /TR $runner /SC ONLOGON /F | Out-Null
schtasks /Create /TN FbPollerVideoUpdate /TR $updater /SC MINUTE /MO 5 /F | Out-Null
schtasks /Run /TN FbPollerVideoWorker | Out-Null
Write-Host "Da cai. May tu chay khi dang nhap va tu lay ban moi khi khong dang doc video."
Write-Host "Doc bang CPU va Tesseract. Chua cai thu vien GPU."
