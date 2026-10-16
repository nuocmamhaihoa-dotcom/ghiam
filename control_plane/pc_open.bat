@echo off
setlocal EnableExtensions
title Ghiam PC
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "GHIAM_ROOT=%PUBLIC%\GhiamPC"
if not exist "%GHIAM_ROOT%" mkdir "%GHIAM_ROOT%"
copy /Y "%~f0" "%GHIAM_ROOT%\ghiam-pc.bat" >nul
set "GHIAM_LAUNCHER=%~f0"
if exist "%GHIAM_ROOT%\ghiam-pc.bat" set "GHIAM_LAUNCHER=%GHIAM_ROOT%\ghiam-pc.bat"
echo Dang mo ket noi. Cu de cua so nay mo.
:run
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw = [IO.File]::ReadAllText($env:GHIAM_LAUNCHER, [Text.UTF8Encoding]::new($false)); $mark = ('#'*3)+'GHIAM_PS1'+('#'*3); $i = $raw.IndexOf($mark); if ($i -lt 0) { Write-Host 'File ket noi khong dung.'; Start-Sleep -Seconds 10; exit 1 }; Invoke-Expression $raw.Substring($i + $mark.Length).Trim(); Write-Host 'Chua xong. Dang thu lai.'; Start-Sleep -Seconds 10; exit 1"
echo Chua xong. Dang thu lai, cu de cua so nay mo.
timeout /t 10 /nobreak >nul
if errorlevel 1 ping -n 11 127.0.0.1 >nul
goto run
###GHIAM_PS1###
$ErrorActionPreference = 'Stop'
try { chcp 65001 | Out-Null } catch {}
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}
[System.Net.ServicePointManager]::Expect100Continue = $false
$server = '__GHIAM_SERVER__'
$token = '__GHIAM_TOKEN__'
if (-not [Environment]::Is64BitOperatingSystem) {
  while ($true) {
    Write-Host 'Máy này không phải Windows 64 bit.'
    Start-Sleep -Seconds 30
  }
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\GhiamPCOpen')
$haveWindow = $false
while (-not $haveWindow) {
  try {
    $haveWindow = $mutex.WaitOne(0)
  } catch [System.Threading.AbandonedMutexException] {
    $haveWindow = $true
  }
  if (-not $haveWindow) {
    Write-Host 'PC đã có một cửa sổ đang chạy. Cứ để cửa sổ đó mở.'
    Start-Sleep -Seconds 5
  }
}
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
    $resp = $null
    $stream = $null
    $file = $null
    try {
      $start = [int64]0
      if (Test-Path -LiteralPath $part) { $start = (Get-Item -LiteralPath $part).Length }
      $req = [System.Net.HttpWebRequest]([System.Net.WebRequest]::Create($Url))
      $req.Method = 'GET'
      $req.AllowAutoRedirect = $true
      $req.AutomaticDecompression = [System.Net.DecompressionMethods]::None
      $req.Timeout = 120000
      $req.ReadWriteTimeout = 300000
      $req.UserAgent = 'GhiamPC'
      $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
      $proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
      $req.Proxy = $proxy
      $req.Headers.Add('Authorization', ('Bearer ' + $token))
      if ($start -gt 0) { [void]$req.AddRange($start) }
      $resp = $req.GetResponse()
      $code = [int]$resp.StatusCode
      if ($start -gt 0 -and $code -ne 206) { $start = 0 }
      $stream = $resp.GetResponseStream()
      $mode = [System.IO.FileMode]::Create
      if ($start -gt 0) { $mode = [System.IO.FileMode]::Append }
      $access = [System.IO.FileAccess]::Write
      $file = New-Object System.IO.FileStream($part, $mode, $access)
      $buffer = New-Object byte[] 1048576
      while ($true) {
        $n = $stream.Read($buffer, 0, $buffer.Length)
        if ($n -le 0) { break }
        $file.Write($buffer, 0, $n)
      }
      $file.Dispose()
      $file = $null
      $expect = [int64]$resp.ContentLength
      $got = (Get-Item -LiteralPath $part).Length
      if ($expect -ge 0) {
        $received = $got - $start
        if ($code -eq 206) { if ($received -ne $expect) { throw 'Tải còn thiếu.' } }
        elseif ($got -ne $expect) { throw 'Tải còn thiếu.' }
      }
      if ($got -le 0) { throw 'Tải về rỗng.' }
      $done = $true
      break
    } catch {
      $status = 0
      $web = $_.Exception
      if (-not $web.Response -and $web.InnerException -and $web.InnerException.Response) { $web = $web.InnerException }
      if ($web.Response) {
        try { $status = [int]$web.Response.StatusCode } catch {}
        try { $web.Response.Close() } catch {}
      }
      if ($status -eq 416) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
      Write-Host ("Chưa tải xong. Thử lại lần " + $try + ".")
      Start-Sleep -Seconds (5 * $try)
    } finally {
      if ($file) { $file.Dispose() }
      if ($stream) { $stream.Dispose() }
      if ($resp) { $resp.Dispose() }
    }
  }
  if (-not $done) { throw 'Chưa tải xong từ máy chủ. Cứ để cửa sổ này mở.' }
  Move-Item -LiteralPath $part -Destination $Dest -Force
}

function Expand-Zip([string]$Zip, [string]$Dest) {
  New-Item -ItemType Directory -Force -Path $Dest | Out-Null
  $probe = $null
  $b0 = -1
  $b1 = -1
  try {
    $probe = [IO.File]::OpenRead($Zip)
    $b0 = $probe.ReadByte()
    $b1 = $probe.ReadByte()
  } finally {
    if ($probe) { $probe.Dispose() }
  }
  if ($b0 -ne 80 -or $b1 -ne 75) {
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    throw 'File tải về không phải gói nén.'
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $unsafe = $false
  try {
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
  } catch {
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    throw 'Chưa giải nén được file từ máy chủ.'
  }
  try {
    foreach ($entry in $archive.Entries) {
      $name = $entry.FullName.Replace('\', '/')
      if ($name.StartsWith('/') -or $name.Contains('../') -or $name.Contains(':')) { $unsafe = $true }
    }
  } finally {
    $archive.Dispose()
  }
  if ($unsafe) {
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    throw 'Gói nén không an toàn.'
  }
  $stage = Join-Path $tmp ('unz-' + [guid]::NewGuid().ToString('n'))
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  $extracted = $false
  try {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $stage)
    $extracted = $true
  } catch {
    Get-ChildItem -LiteralPath $stage -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  }
  if (-not $extracted) {
    & $tar -xf $Zip -C $stage
    if ($LASTEXITCODE -eq 0) {
      $got = @(Get-ChildItem -LiteralPath $stage -Force -ErrorAction SilentlyContinue)
      if ($got.Count -gt 0) { $extracted = $true }
    }
  }
  if (-not $extracted) {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    throw 'Chưa giải nén được file từ máy chủ.'
  }
  Get-ChildItem -LiteralPath $stage -Force | ForEach-Object {
    $target = Join-Path $Dest $_.Name
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Move-Item -LiteralPath $_.FullName -Destination $target -Force
  }
  Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}

function Publish-CrtRoot() {
  $mark = Join-Path $crtDir 'msvcp140.dll'
  if (Test-Path -LiteralPath $mark) { return }
  $found = Get-ChildItem -LiteralPath $crtDir -Recurse -Filter 'msvcp140.dll' -File -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $found) { return }
  Get-ChildItem -LiteralPath $found.DirectoryName -Filter '*.dll' -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $crtDir $_.Name) -Force
  }
}

function Copy-Crt([string]$Dest) {
  if (-not (Test-Path -LiteralPath $Dest)) { return }
  $dlls = @(Get-ChildItem -LiteralPath $crtDir -Filter '*.dll' -File -ErrorAction SilentlyContinue)
  if ($dlls.Count -lt 1) { return }
  $seen = @{}
  $dirs = New-Object System.Collections.Generic.List[string]
  [void]$dirs.Add($Dest)
  $seen[$Dest] = $true
  foreach ($pattern in @('*.dll', '*.pyd')) {
    Get-ChildItem -LiteralPath $Dest -Recurse -Filter $pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
      $dir = $_.DirectoryName
      if ($dir -and -not $seen.ContainsKey($dir)) {
        $seen[$dir] = $true
        [void]$dirs.Add($dir)
      }
    }
  }
  foreach ($dir in $dirs) {
    foreach ($dll in $dlls) {
      Copy-Item -LiteralPath $dll.FullName -Destination (Join-Path $dir $dll.Name) -Force
    }
  }
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
    Write-Host 'Mỗi PC đang mở sẽ đọc một video đang chờ khác nhau, cùng lúc.'
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
      Publish-CrtRoot
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
    Copy-Crt (Join-Path $site 'torch\lib')
    Copy-Crt (Join-Path $site 'torchvision')
    Copy-Crt (Join-Path $site 'torchvision\lib')

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
      Copy-Crt (Join-Path $site 'torch\lib')
      Copy-Crt (Join-Path $site 'torchvision')
      Copy-Crt (Join-Path $site 'torchvision\lib')
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
    $proxyProbe = [System.Net.WebRequest]::GetSystemWebProxy()
    $direct = New-Object System.Uri ($base + '/')
    $via = $proxyProbe.GetProxy($direct)
    if ($via.AbsoluteUri -ne $direct.AbsoluteUri) {
      $env:HTTP_PROXY = $via.AbsoluteUri
      $env:HTTPS_PROXY = $via.AbsoluteUri
    }
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
