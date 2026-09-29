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
    (Join-Path $Root "Tesseract-OCR\tesseract.exe"),
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
  )) {
    if ($path -and (Test-Path $path)) { return $path }
  }
  $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

function Save-FirstWorkingUrl {
  param([string[]]$Urls, [string]$Dest, [int]$MinBytes)
  foreach ($url in $Urls) {
    try {
      Write-Host "Tai $url"
      Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $Dest
      if ((Test-Path $Dest) -and ((Get-Item $Dest).Length -ge $MinBytes)) { return }
    } catch {
      if (Test-Path $Dest) { Remove-Item $Dest -Force -ErrorAction SilentlyContinue }
    }
  }
  throw "Chua tai duoc file ve may."
}

function Read-HubConfig {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $null }
  $raw = [System.IO.File]::ReadAllText($Path)
  if (-not $raw) { return $null }
  return $raw | ConvertFrom-Json
}

$cfgPath = Join-Path $Root "config.json"
$token = [string]$env:CONTROL_TOKEN
$besideCfg = $null
if ($PSScriptRoot) { $besideCfg = Join-Path $PSScriptRoot "config.json" }
if (-not $token -and $besideCfg) {
  $bundled = Read-HubConfig $besideCfg
  if ($bundled) {
    $token = [string]$bundled.token
    if (-not $env:CONTROL_HUB -and $bundled.hub) { $Hub = ([string]$bundled.hub).TrimEnd("/") }
  }
}
if (-not $token -and (Test-Path $cfgPath)) {
  $existing = Read-HubConfig $cfgPath
  if ($existing) {
    $token = [string]$existing.token
    if (-not $env:CONTROL_HUB -and $existing.hub) { $Hub = ([string]$existing.hub).TrimEnd("/") }
  }
}
if (-not $token) { throw "Goi cai thieu token. Mo lai FbPoller.bat tai tu trang hub." }

Write-Host "Cai cong cu doc video vao $Root"
Write-Host "Hub $Hub"

$ffmpegDir = Join-Path $Root "tools\ffmpeg"
if (Test-Path (Join-Path $ffmpegDir "ffmpeg.exe")) {
  $env:Path = "$ffmpegDir;" + $env:Path
}
$tesseractDir = Join-Path $Root "Tesseract-OCR"
if (Test-Path (Join-Path $tesseractDir "tesseract.exe")) {
  $env:Path = "$tesseractDir;" + $env:Path
}

if (-not (Find-PythonExe)) {
  Write-Host "Cai Python"
  $pythonSetup = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
  Save-FirstWorkingUrl -Urls @(
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
  ) -Dest $pythonSetup -MinBytes 10000000
  $pythonInstall = Start-Process -FilePath $pythonSetup -ArgumentList "/quiet","InstallAllUsers=0","PrependPath=1","Include_test=0","Include_pip=1","Include_launcher=0" -Wait -PassThru
  if ($pythonInstall.ExitCode -ne 0) { throw "Chua cai duoc Python." }
  Update-SessionPath
}
$python = Find-PythonExe
if (-not $python) { throw "Chua cai duoc Python." }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Cai ffmpeg"
  New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
  $ffmpegZip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
  Save-FirstWorkingUrl -Urls @(
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
  ) -Dest $ffmpegZip -MinBytes 20000000
  $unpack = Join-Path $env:TEMP "ffmpeg-unpack"
  if (Test-Path $unpack) { Remove-Item $unpack -Recurse -Force }
  Expand-Archive -Path $ffmpegZip -DestinationPath $unpack -Force
  $ffmpegExe = Get-ChildItem $unpack -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $ffmpegExe) { throw "Chua tai duoc ffmpeg." }
  Copy-Item (Join-Path $ffmpegExe.DirectoryName "*") $ffmpegDir -Force
  $env:Path = "$ffmpegDir;" + $env:Path
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "Chua cai duoc ffmpeg." }

if (-not (Find-TesseractExe)) {
  Write-Host "Cai Tesseract"
  New-Item -ItemType Directory -Force -Path $tesseractDir | Out-Null
  $tesseractSetup = Join-Path $env:TEMP "tesseract-ocr-w64-setup.exe"
  Save-FirstWorkingUrl -Urls @(
    "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
  ) -Dest $tesseractSetup -MinBytes 20000000
  $tesseractInstall = Start-Process -FilePath $tesseractSetup -ArgumentList "/VERYSILENT /NORESTART /DIR=`"$tesseractDir`"" -Wait -PassThru
  if ($tesseractInstall.ExitCode -ne 0 -and -not (Test-Path (Join-Path $tesseractDir "tesseract.exe"))) {
    throw "Chua cai duoc Tesseract."
  }
  $env:Path = "$tesseractDir;" + $env:Path
}
if (-not (Find-TesseractExe)) { throw "Chua cai duoc Tesseract." }

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
  $beside = $PSScriptRoot
  if ($beside) {
    $local = Join-Path $beside (Split-Path -Leaf $Dest)
    if ((Test-Path $local) -and $local -ne $Dest) {
      Copy-Item $local $Dest -Force
    }
  }
  Write-Host "Tai $Url"
  try {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Dest
  } catch {
    if (-not (Test-Path $Dest)) { throw }
    Write-Host "Giu ban nam trong goi tai ve."
  }
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
$pillowReady = $false
& $venvPy -c "import PIL"
if ($LASTEXITCODE -eq 0) { $pillowReady = $true }
if (-not $pillowReady) {
  & $venvPy -m pip install --upgrade pip
  & $venvPy -m pip install pillow
  if ($LASTEXITCODE -ne 0) { throw "Chua cai duoc pillow." }
}

try { schtasks /End /TN FbPollerVideoWorker | Out-Null } catch { }
try { schtasks /Delete /TN FbPollerVideoWorker /F | Out-Null } catch { }
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
$code = $LASTEXITCODE
$marker = Join-Path $Root "watchdog-replaced"
if ($code -ne 0 -and (Test-Path $marker)) {
  $prev = Join-Path $Root "video_watchdog.py.prev"
  if (Test-Path $prev) {
    Copy-Item $prev (Join-Path $Root "video_watchdog.py") -Force
  }
  Remove-Item $marker -Force -ErrorAction SilentlyContinue
}
exit $code
'@ | Set-Content -Encoding utf8 (Join-Path $Root "Update-VideoWorker.ps1")

$updater = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\Update-VideoWorker.ps1`""
function Register-KeepAliveTask {
  param([string]$Name, [string]$Execute, [string]$Kind)
  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Execute
  if ($Kind -eq "logon") {
    $trigger = New-ScheduledTaskTrigger -AtLogOn
  } else {
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::FromDays(9999))
  }
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}
try {
  Register-KeepAliveTask -Name "FbPollerVideoUpdate" -Execute "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\Update-VideoWorker.ps1`"" -Kind "update"
} catch {
  schtasks /Create /TN FbPollerVideoUpdate /TR $updater /SC MINUTE /MO 5 /F | Out-Null
  Write-Host "Lich cap nhat de sau. Cua so nay van noi hub."
}
Write-Host "Da cai. Cua so nay se noi hub. Dang nhap sau thi tu mo lai."
Write-Host "Doc bang CPU va Tesseract. Chua cai thu vien GPU."
