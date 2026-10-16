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
try {
  $server = '__GHIAM_SERVER__'
  $token = '__GHIAM_TOKEN__'
  if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'Máy này không phải Windows 64 bit.'
  }
  Write-Host 'PC đang tự cài phần đọc và nối máy chủ. Cứ để cửa sổ này mở.'
  Write-Host 'Lần đầu có thể mất một lúc. Không cần gõ địa chỉ, không cần giải nén.'
  $root = Join-Path $env:LOCALAPPDATA 'GhiamPC'
  $tools = Join-Path $root 'tools'
  New-Item -ItemType Directory -Force -Path $tools | Out-Null

  function Save-Url([string]$Url, [string]$Dest, [string]$Label) {
    Write-Host $Label
    & curl.exe -fL --retry 5 --retry-delay 2 -o $Dest $Url
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Dest)) {
      throw 'Chưa tải xong. Kiểm tra mạng rồi mở lại file này.'
    }
  }

  $pyDir = Join-Path $tools 'python'
  $pyExe = Join-Path $pyDir 'python.exe'
  $ready = Join-Path $pyDir 'ready.txt'
  if (-not (Test-Path -LiteralPath $ready)) {
    New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
    if (-not (Test-Path -LiteralPath $pyExe)) {
      $zip = Join-Path $tools 'python.zip'
      Save-Url 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' $zip 'Đang tải Python.'
      & tar.exe -xf $zip -C $pyDir
      if ($LASTEXITCODE -ne 0) { throw 'Chưa giải nén được Python.' }
    }
    $pth = Get-ChildItem -LiteralPath $pyDir -Filter 'python*._pth' | Select-Object -First 1
    if (-not $pth) { throw 'Bản Python tải về không đúng.' }
    $lines = [IO.File]::ReadAllLines($pth.FullName)
    $out = foreach ($line in $lines) {
      if ($line -match '^\s*#\s*import site\s*$') { 'import site' } else { $line }
    }
    if ($out -notcontains 'Lib\site-packages') { $out += 'Lib\site-packages' }
    [IO.File]::WriteAllLines($pth.FullName, [string[]]$out)
    $getPip = Join-Path $tools 'get-pip.py'
    Save-Url 'https://bootstrap.pypa.io/get-pip.py' $getPip 'Đang chuẩn bị thư viện Python.'
    & $pyExe $getPip --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw 'Chưa cài được pip.' }
    & $pyExe -m pip install --disable-pip-version-check pillow numpy
    if ($LASTEXITCODE -ne 0) { throw 'Chưa cài được thư viện đọc ảnh.' }
    Set-Content -LiteralPath $ready -Value '1' -Encoding ascii
  }

  $ffRoot = Join-Path $tools 'ffmpeg-src'
  $ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $ff) {
    New-Item -ItemType Directory -Force -Path $ffRoot | Out-Null
    $zip = Join-Path $tools 'ffmpeg.zip'
    Save-Url 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $zip 'Đang tải ffmpeg.'
    & tar.exe -xf $zip -C $ffRoot
    if ($LASTEXITCODE -ne 0) { throw 'Chưa giải nén được ffmpeg.' }
    $ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $ff) { throw 'Chưa có ffmpeg.' }
  }

  $tessDir = Join-Path $tools 'tesseract'
  $tessExe = Join-Path $tessDir 'tesseract.exe'
  if (-not (Test-Path -LiteralPath $tessExe)) {
    $setup = Join-Path $tools 'tesseract-setup.exe'
    if (-not (Test-Path -LiteralPath $setup)) {
      Save-Url 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' $setup 'Đang tải bộ đọc chữ.'
    }
    New-Item -ItemType Directory -Force -Path $tessDir | Out-Null
    $target = $tessDir
    try {
      $short = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($tessDir).ShortPath
      if ($short) { $target = $short }
    } catch {}
    Write-Host 'Nếu Windows hỏi quyền cài, bấm Có. Không cần gõ gì.'
    Start-Process -FilePath $setup -ArgumentList @('/S', "/D=$target") -Wait
    if (-not (Test-Path -LiteralPath $tessExe)) {
      $alt = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
      if (Test-Path -LiteralPath $alt) { $tessExe = $alt } else { throw 'Chưa cài được bộ đọc chữ.' }
    }
  }

  $env:Path = (Split-Path -Parent $pyExe) + ';' + (Split-Path -Parent $ff.FullName) + ';' + (Split-Path -Parent $tessExe) + ';' + $env:Path
  $script = Join-Path $root 'ghiam-pc.py'
  $part = $script + '.part'
  Write-Host 'Đang lấy chương trình nối mới nhất.'
  & curl.exe -fL --retry 5 --retry-delay 2 -H "Authorization: Bearer $token" -o $part ($server.TrimEnd('/') + '/v1/pc/connector.py')
  if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $part)) {
    $head = [IO.File]::ReadAllText($part)
    if ($head.Contains('def main')) {
      Move-Item -LiteralPath $part -Destination $script -Force
    } else {
      Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    }
  }
  if (-not (Test-Path -LiteralPath $script)) {
    throw 'Chưa tải được chương trình nối. Mở lại file này khi mạng ổn.'
  }
  Set-Location -LiteralPath $root
  while ($true) {
    & $pyExe $script
    if ($LASTEXITCODE -ne 2) { exit $LASTEXITCODE }
    Write-Host 'Máy chủ có bản mới. PC đang chạy lại.'
  }
} catch {
  Write-Host $_.Exception.Message
  exit 1
}
