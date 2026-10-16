@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "GHIAM_LAUNCHER=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw = [IO.File]::ReadAllText($env:GHIAM_LAUNCHER, [Text.UTF8Encoding]::new($false)); $mark = '###GHIAM_PS1###'; $i = $raw.IndexOf($mark); if ($i -lt 0) { Write-Host 'File ket noi khong dung.'; exit 1 }; Invoke-Expression $raw.Substring($i + $mark.Length).Trim(); if ($null -eq $LASTEXITCODE) { exit 0 } else { exit $LASTEXITCODE }"
echo.
echo Dong cua so nay la PC ngung giup doc. Mo lai file nay khi muon PC doc tiep.
pause
exit /b 0
###GHIAM_PS1###
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
$server = '__GHIAM_SERVER__'
$token = '__GHIAM_TOKEN__'
if (-not [Environment]::Is64BitOperatingSystem) {
  Write-Host 'Máy này không phải Windows 64 bit.'
  exit 1
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\GhiamPC')
$haveWindow = $false
try {
  $haveWindow = $mutex.WaitOne(0)
} catch [System.Threading.AbandonedMutexException] {
  $haveWindow = $true
}
if (-not $haveWindow) {
  Write-Host 'PC đã có một cửa sổ đang chạy. Cứ để cửa sổ đó mở.'
  exit 0
}
$curl = Join-Path $env:SystemRoot 'System32\curl.exe'
if (-not (Test-Path -LiteralPath $curl)) { $curl = 'curl.exe' }
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path -LiteralPath $tar)) { $tar = 'tar.exe' }
$root = Join-Path $env:PUBLIC 'GhiamPC'
$tools = Join-Path $root 'tools'
$tmp = Join-Path $root 'tmp'
$env:TEMP = $tmp
$env:TMP = $tmp
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$base = $server.TrimEnd('/')

function Save-Url([string]$Url, [string]$Dest, [string]$Label) {
  Write-Host $Label
  $part = $Dest + '.part'
  $done = $false
  for ($try = 1; $try -le 8; $try++) {
    $before = 0
    if (Test-Path -LiteralPath $part) { $before = (Get-Item -LiteralPath $part).Length }
    & $curl -fL --retry 3 --retry-delay 2 -C - --speed-time 60 --speed-limit 1000 -H "Authorization: Bearer $token" -o $part $Url
    $after = 0
    if (Test-Path -LiteralPath $part) { $after = (Get-Item -LiteralPath $part).Length }
    if ($LASTEXITCODE -eq 0 -and $after -gt 0) { $done = $true; break }
    if ($after -le $before) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
    Write-Host "Chưa tải xong. Thử lại lần $try."
    Start-Sleep -Seconds (5 * $try)
  }
  if (-not $done) { throw 'Chưa tải xong từ máy chủ. Cứ để cửa sổ này mở.' }
  Move-Item -LiteralPath $part -Destination $Dest -Force
}

function Expand-Zip([string]$Zip, [string]$Dest) {
  New-Item -ItemType Directory -Force -Path $Dest | Out-Null
  & $tar -xf $Zip -C $Dest
  if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    throw 'Chưa giải nén được file từ máy chủ.'
  }
}

function Copy-Crt([string]$Dest) {
  if (-not (Test-Path -LiteralPath $Dest)) { return }
  Copy-Item -Path (Join-Path $crtDir '*.dll') -Destination $Dest -Force
}

$script = Join-Path $root 'ghiam-pc.py'
$pyDir = Join-Path $tools 'python'
$pyExe = Join-Path $pyDir 'python.exe'
$ready = Join-Path $pyDir 'ready.txt'
$runtimeMark = Join-Path $pyDir 'runtime-ready.txt'
$ffRoot = Join-Path $tools 'ffmpeg-src'
$tessDir = Join-Path $tools 'tesseract'
$tessExe = Join-Path $tessDir 'tesseract.exe'

$crtDir = Join-Path $tools 'crt'

while ($true) {
  try {
    New-Item -ItemType Directory -Force -Path $tools, $tmp | Out-Null
    Write-Host 'PC đang tự cài phần đọc và nối máy chủ. Cứ để cửa sổ này mở.'
    Write-Host 'Lần đầu có thể mất một lúc. Không cần gõ địa chỉ, không cần giải nén.'
    Save-Url ($base + '/v1/pc/connector.py') $script 'Đang lấy chương trình nối.'
    if (-not ([IO.File]::ReadAllText($script).Contains('def main'))) {
      throw 'Chương trình nối tải về không đúng.'
    }

    $crtMark = Join-Path $crtDir 'msvcp140.dll'
    if (-not (Test-Path -LiteralPath $crtMark)) {
      $crtZip = Join-Path $tools 'crt.zip'
      Save-Url ($base + '/v1/pc/tools/crt.zip') $crtZip 'Đang tải thư viện Windows từ máy chủ.'
      if (Test-Path -LiteralPath $crtDir) { Remove-Item -LiteralPath $crtDir -Recurse -Force }
      Expand-Zip $crtZip $crtDir
    }
    if (-not (Test-Path -LiteralPath $crtMark)) { throw 'Thư viện Windows tải về không đúng.' }

    $pythonOk = (Test-Path -LiteralPath $ready) -and (Test-Path -LiteralPath $pyExe)
    if ($pythonOk) {
      Copy-Crt $pyDir
      & $pyExe -c 'import PIL, numpy'
      if ($LASTEXITCODE -ne 0) {
        $pythonOk = $false
        Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
        & $pyExe -c 'print(1)'
        if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $pyDir -Recurse -Force -ErrorAction SilentlyContinue }
      }
    }
    if (-not $pythonOk) {
      New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
      if (-not (Test-Path -LiteralPath $pyExe)) {
        $zip = Join-Path $tools 'python.zip'
        Save-Url ($base + '/v1/pc/tools/python.zip') $zip 'Đang tải Python từ máy chủ.'
        Expand-Zip $zip $pyDir
      }
      Copy-Crt $pyDir
      $pth = Get-ChildItem -LiteralPath $pyDir -Filter 'python*._pth' | Select-Object -First 1
      if (-not $pth) { throw 'Bản Python tải về không đúng.' }
      $lines = [IO.File]::ReadAllLines($pth.FullName)
      $out = New-Object System.Collections.Generic.List[string]
      foreach ($line in $lines) {
        if ($line -match '^\s*#\s*import site\s*$') { [void]$out.Add('import site') } else { [void]$out.Add($line) }
      }
      if (-not $out.Contains('Lib\site-packages')) { [void]$out.Add('Lib\site-packages') }
      $utf8 = New-Object System.Text.UTF8Encoding $false
      [IO.File]::WriteAllLines($pth.FullName, $out.ToArray(), $utf8)
      $getPip = Join-Path $tools 'get-pip.py'
      Save-Url ($base + '/v1/pc/tools/get-pip.py') $getPip 'Đang chuẩn bị thư viện Python.'
      & $pyExe $getPip --no-warn-script-location
      if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $pyDir -Recurse -Force -ErrorAction SilentlyContinue
        throw 'Chưa cài được pip.'
      }
      $wheels = Join-Path $tools 'wheels.zip'
      $wheelDir = Join-Path $tools 'wheels'
      Save-Url ($base + '/v1/pc/tools/wheels.zip') $wheels 'Đang tải thư viện đọc ảnh từ máy chủ.'
      if (Test-Path -LiteralPath $wheelDir) { Remove-Item -LiteralPath $wheelDir -Recurse -Force }
      Expand-Zip $wheels $wheelDir
      & $pyExe -m pip install --disable-pip-version-check --no-index --no-deps --find-links $wheelDir pillow numpy
      if ($LASTEXITCODE -ne 0) { throw 'Chưa cài được thư viện đọc ảnh.' }
      & $pyExe -c 'import PIL, numpy'
      if ($LASTEXITCODE -ne 0) { throw 'Thư viện đọc ảnh chưa chạy được.' }
      Set-Content -LiteralPath $ready -Value '1' -Encoding ascii
    }

    $site = Join-Path $pyDir 'Lib\site-packages'
    Copy-Crt $pyDir
    Copy-Crt (Join-Path $site 'torch')
    Copy-Crt (Join-Path $site 'torchvision')

    $runtimeOk = Test-Path -LiteralPath $runtimeMark
    if ($runtimeOk) {
      & $pyExe -c 'import torch, torchvision, einops, yaml, gdown, vietocr'
      if ($LASTEXITCODE -ne 0) { $runtimeOk = $false; Remove-Item -LiteralPath $runtimeMark -Force -ErrorAction SilentlyContinue }
    }
    if (-not $runtimeOk) {
      $runtime = Join-Path $tools 'runtime.zip'
      $runtimeDir = Join-Path $tools 'runtime'
      if (-not (Test-Path -LiteralPath $runtime)) {
        Save-Url ($base + '/v1/pc/tools/runtime.zip') $runtime 'Đang tải bộ đọc dấu từ máy chủ.'
      }
      if (Test-Path -LiteralPath $runtimeDir) { Remove-Item -LiteralPath $runtimeDir -Recurse -Force }
      Expand-Zip $runtime $runtimeDir
      $runtimeWheels = @(Get-ChildItem -LiteralPath $runtimeDir -Filter '*.whl' | ForEach-Object { $_.FullName })
      if ($runtimeWheels.Count -lt 1) { throw 'Bộ đọc dấu tải về không có gói.' }
      & $pyExe -m pip install --disable-pip-version-check --no-index --no-deps --find-links $runtimeDir @runtimeWheels
      if ($LASTEXITCODE -ne 0) { throw 'Chưa cài được bộ đọc dấu.' }
      Copy-Crt $pyDir
      Copy-Crt (Join-Path $site 'torch')
      Copy-Crt (Join-Path $site 'torchvision')
      & $pyExe -c 'import torch, torchvision, einops, yaml, gdown, vietocr'
      if ($LASTEXITCODE -ne 0) { throw 'Bộ đọc dấu chưa chạy được.' }
      Set-Content -LiteralPath $runtimeMark -Value '1' -Encoding ascii
    }

    $ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ff) {
      & $ff.FullName -version | Out-Null
      if ($LASTEXITCODE -ne 0) { $ff = $null; Remove-Item -LiteralPath $ffRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not $ff) {
      New-Item -ItemType Directory -Force -Path $ffRoot | Out-Null
      $zip = Join-Path $tools 'ffmpeg.zip'
      Save-Url ($base + '/v1/pc/tools/ffmpeg.zip') $zip 'Đang tải ffmpeg từ máy chủ.'
      Expand-Zip $zip $ffRoot
      $ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
      if (-not $ff) { throw 'Chưa có ffmpeg.' }
      & $ff.FullName -version | Out-Null
      if ($LASTEXITCODE -ne 0) { throw 'Chưa chạy được ffmpeg.' }
    }

    $tessOk = Test-Path -LiteralPath $tessExe
    if ($tessOk) {
      & $tessExe --version | Out-Null
      if ($LASTEXITCODE -ne 0) { $tessOk = $false; Remove-Item -LiteralPath $tessDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (-not $tessOk) {
      $tessZip = Join-Path $tools 'tesseract.zip'
      Save-Url ($base + '/v1/pc/tools/tesseract.zip') $tessZip 'Đang tải bộ đọc chữ từ máy chủ.'
      if (Test-Path -LiteralPath $tessDir) { Remove-Item -LiteralPath $tessDir -Recurse -Force }
      Expand-Zip $tessZip $tessDir
      if (-not (Test-Path -LiteralPath $tessExe)) { throw 'Bộ đọc chữ tải về không đúng.' }
      & $tessExe --version | Out-Null
      if ($LASTEXITCODE -ne 0) { throw 'Chưa chạy được bộ đọc chữ.' }
    }

    if (-not $script:basePath) { $script:basePath = $env:Path }
    $env:Path = (Split-Path -Parent $pyExe) + ';' + (Split-Path -Parent $ff.FullName) + ';' + (Split-Path -Parent $tessExe) + ';' + $script:basePath
    Set-Location -LiteralPath $root
    & $pyExe $script
    if ($LASTEXITCODE -eq 2) {
      Write-Host 'Máy chủ có bản mới. PC đang chạy lại.'
      continue
    }
    Write-Host 'PC đang chạy lại. Cứ để cửa sổ này mở.'
    Start-Sleep -Seconds 15
  } catch {
    Write-Host $_.Exception.Message
    Write-Host 'Chưa xong. Đang thử lại, cứ để cửa sổ này mở.'
    Start-Sleep -Seconds 15
  }
}
