# GHIAM-OPEN 1
# Máy chủ phát file này. Cửa sổ ghiam-pc.bat tải lại nó trước mỗi lượt chạy,
# nên sửa ở đây là mọi PC đang mở tự nhận, không cần tải file mới.
# File .bat đặt sẵn $server, $token, $base, $root và $basePath.
# Không dùng lệnh exit ở đây: exit tắt luôn cửa sổ PC.

$tools = Join-Path $root 'tools'
$tmp = Join-Path $root 'tmp'
$env:TEMP = $tmp
$env:TMP = $tmp
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path -LiteralPath $tar)) { $tar = 'tar.exe' }

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

# Giải nén bằng thư viện có sẵn của Windows; tar chỉ là đường dự phòng.
# Giải vào thư mục tạm rồi mới chuyển sang, nên gói hỏng không làm hỏng bản đang có.
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
  Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
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

# Chạy thử một chương trình. File hỏng tới mức không mở được cũng chỉ trả false, để bước sau tải lại.
function Invoke-Ok([string]$Exe, [string[]]$Arguments) {
  try {
    & $Exe @Arguments | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

# Thư viện C++ của Windows đặt cạnh python.exe: Python nạp DLL ở thư mục chương trình trước thư mục hệ thống.
function Copy-Crt([string]$Dest) {
  if (-not (Test-Path -LiteralPath $Dest)) { return }
  Get-ChildItem -LiteralPath $crtDir -Filter '*.dll' -File -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dest $_.Name) -Force
  }
}

$script = Join-Path $root 'ghiam-pc.py'
$pyDir = Join-Path $tools 'python'
$pyExe = Join-Path $pyDir 'python.exe'
$site = Join-Path $pyDir 'Lib\site-packages'
$ready = Join-Path $pyDir 'ready.txt'
$wheelDir = Join-Path $tools 'wheels'
$ffRoot = Join-Path $tools 'ffmpeg-src'
$tessDir = Join-Path $tools 'tesseract'
$tessExe = Join-Path $tessDir 'tesseract.exe'
$crtDir = Join-Path $tools 'crt'

New-Item -ItemType Directory -Force -Path $tools, $tmp | Out-Null
# Python 3.12 và bộ đọc dấu cần Windows 10 hoặc 11 bản 64 bit.
if ([Environment]::OSVersion.Platform -eq 'Win32NT' -and [Environment]::OSVersion.Version.Major -lt 10) {
  Write-Host 'Máy này cần Windows 10 hoặc Windows 11 bản 64 bit để đọc video.'
  Start-Sleep -Seconds 300
  return
}
Write-Host 'PC đang tự cài phần đọc và nối máy chủ. Cứ để cửa sổ này mở.'
Write-Host 'Mỗi PC đang mở sẽ đọc một video đang chờ khác nhau, cùng lúc.'
Write-Host 'Lần đầu có thể mất một lúc. Không cần gõ địa chỉ, không cần giải nén.'

# Bản cài cũ để bộ thư viện đọc dấu ở đây. Giờ chương trình nối tự cài và kiểm theo máy chủ.
foreach ($old in @((Join-Path $tools 'runtime.zip'), (Join-Path $tools 'runtime'), (Join-Path $pyDir 'runtime-ready.txt'), (Join-Path $tools 'get-pip.py'))) {
  if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
}

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
  if (-not (Invoke-Ok $pyExe @('-c', 'import PIL, numpy, pip'))) {
    $pythonOk = $false
    Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  }
}
if (-not $pythonOk) {
  if ((Test-Path -LiteralPath $pyExe) -and -not (Invoke-Ok $pyExe @('-c', 'print(1)'))) {
    Remove-Item -LiteralPath $pyDir -Recurse -Force -ErrorAction SilentlyContinue
  }
  New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
  if (-not (Test-Path -LiteralPath $pyExe)) {
    $zip = Join-Path $tools 'python.zip'
    Save-Url ($base + '/v1/pc/tools/python.zip') $zip 'Đang tải Python từ máy chủ.'
    Expand-Zip $zip $pyDir
  }
  Copy-Crt $pyDir
  if (-not (Invoke-Ok $pyExe @('-c', 'print(1)'))) {
    Remove-Item -LiteralPath $pyDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'Python vừa tải chưa chạy được trên máy này. 5 phút nữa PC tải lại.'
    Start-Sleep -Seconds 300
    throw 'Python tải về chưa chạy được.'
  }
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
  # pip lấy từ gói của máy chủ và chép thẳng vào Python, không cần mạng ngoài.
  $wheels = Join-Path $tools 'wheels.zip'
  Save-Url ($base + '/v1/pc/tools/wheels.zip') $wheels 'Đang tải thư viện đọc ảnh từ máy chủ.'
  if (Test-Path -LiteralPath $wheelDir) { Remove-Item -LiteralPath $wheelDir -Recurse -Force }
  Expand-Zip $wheels $wheelDir
  $pipWheel = Get-ChildItem -LiteralPath $wheelDir -Filter 'pip-*.whl' | Select-Object -First 1
  if (-not $pipWheel) { throw 'Gói thư viện trên máy chủ thiếu pip.' }
  New-Item -ItemType Directory -Force -Path $site | Out-Null
  Get-ChildItem -LiteralPath $site -Force | Where-Object { $_.Name -eq 'pip' -or $_.Name -like 'pip-*.dist-info' } | Remove-Item -Recurse -Force
  Expand-Zip $pipWheel.FullName $site
  & $pyExe -m pip install --disable-pip-version-check --no-warn-script-location --no-index --find-links $wheelDir pillow numpy
  if ($LASTEXITCODE -ne 0) { throw 'Chưa cài được thư viện đọc ảnh.' }
  if (-not (Invoke-Ok $pyExe @('-c', 'import PIL, numpy, pip'))) { throw 'Thư viện đọc ảnh chưa chạy được.' }
  Set-Content -LiteralPath $ready -Value '1' -Encoding ascii
}
Copy-Crt (Join-Path $site 'torch\lib')

$ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ff -and -not (Invoke-Ok $ff.FullName @('-version'))) {
  $ff = $null
  Remove-Item -LiteralPath $ffRoot -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not $ff) {
  New-Item -ItemType Directory -Force -Path $ffRoot | Out-Null
  $zip = Join-Path $tools 'ffmpeg.zip'
  Save-Url ($base + '/v1/pc/tools/ffmpeg.zip') $zip 'Đang tải ffmpeg từ máy chủ.'
  Expand-Zip $zip $ffRoot
  $ff = Get-ChildItem -LiteralPath $ffRoot -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $ff) { throw 'Chưa có ffmpeg.' }
  if (-not (Invoke-Ok $ff.FullName @('-version'))) {
    Remove-Item -LiteralPath $ffRoot -Recurse -Force -ErrorAction SilentlyContinue
    throw 'Chưa chạy được ffmpeg.'
  }
}

$tessOk = (Test-Path -LiteralPath $tessExe) -and (Invoke-Ok $tessExe @('--version'))
if (-not $tessOk) {
  $tessZip = Join-Path $tools 'tesseract.zip'
  Save-Url ($base + '/v1/pc/tools/tesseract.zip') $tessZip 'Đang tải bộ đọc chữ từ máy chủ.'
  if (Test-Path -LiteralPath $tessDir) { Remove-Item -LiteralPath $tessDir -Recurse -Force }
  Expand-Zip $tessZip $tessDir
  if (-not (Test-Path -LiteralPath $tessExe)) { throw 'Bộ đọc chữ tải về không đúng.' }
  if (-not (Invoke-Ok $tessExe @('--version'))) {
    Remove-Item -LiteralPath $tessDir -Recurse -Force -ErrorAction SilentlyContinue
    throw 'Chưa chạy được bộ đọc chữ.'
  }
}

$env:Path = (Split-Path -Parent $pyExe) + ';' + (Split-Path -Parent $ff.FullName) + ';' + (Split-Path -Parent $tessExe) + ';' + $basePath
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
  return
}
Write-Host 'PC đang chạy lại. Cứ để cửa sổ này mở.'
Start-Sleep -Seconds 15
