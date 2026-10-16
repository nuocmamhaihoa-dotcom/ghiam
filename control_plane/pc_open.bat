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
$mutex = New-Object System.Threading.Mutex($false, 'Local\GhiamPCWindow')
$haveWindow = $false
$told = $false
while (-not $haveWindow) {
  try {
    $haveWindow = $mutex.WaitOne(0)
  } catch [System.Threading.AbandonedMutexException] {
    $haveWindow = $true
  }
  if (-not $haveWindow) {
    if (-not $told) {
      Write-Host 'PC này đã có một cửa sổ kết nối khác đang mở. Cửa sổ này chờ, có thể đóng nó.'
      $told = $true
    }
    Start-Sleep -Seconds 5
  }
}
$root = Join-Path $env:PUBLIC 'GhiamPC'
$base = $server.TrimEnd('/')
$basePath = $env:Path
$installer = Join-Path $root 'ghiam-open.ps1'
$utf8 = New-Object System.Text.UTF8Encoding $false
New-Item -ItemType Directory -Force -Path $root | Out-Null

# File này tải về một lần. Phần cài nằm trên máy chủ và được tải lại mỗi lượt,
# nên máy chủ sửa phần cài là cửa sổ đang mở tự nhận ở lượt sau.
function Get-Installer {
  $req = [System.Net.HttpWebRequest]([System.Net.WebRequest]::Create($base + '/v1/pc/open.ps1'))
  $req.Method = 'GET'
  $req.AutomaticDecompression = [System.Net.DecompressionMethods]::None
  $req.Timeout = 60000
  $req.ReadWriteTimeout = 120000
  $req.UserAgent = 'GhiamPC'
  $proxy = [System.Net.WebRequest]::GetSystemWebProxy()
  $proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
  $req.Proxy = $proxy
  $req.Headers.Add('Authorization', ('Bearer ' + $token))
  $resp = $req.GetResponse()
  try {
    $memory = New-Object System.IO.MemoryStream
    $resp.GetResponseStream().CopyTo($memory)
    $text = $utf8.GetString($memory.ToArray())
  } finally {
    $resp.Close()
  }
  if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
  if (-not $text.StartsWith('# GHIAM-OPEN')) { throw 'Bản cài tải về không đúng.' }
  return $text
}

while ($true) {
  $block = $null
  try {
    $fresh = Get-Installer
    $block = [scriptblock]::Create($fresh)
    [IO.File]::WriteAllText($installer, $fresh, $utf8)
  } catch {
    Write-Host 'Chưa lấy được bản cài mới từ máy chủ. Dùng bản đã lưu nếu có.'
    if (Test-Path -LiteralPath $installer) {
      try { $block = [scriptblock]::Create([IO.File]::ReadAllText($installer, $utf8)) } catch { $block = $null }
    }
  }
  if (-not $block) {
    Write-Host 'Chưa nối được máy chủ. Cửa sổ tự thử lại, cứ để mở.'
    Start-Sleep -Seconds 15
    continue
  }
  try {
    & $block
  } catch {
    Write-Host $_.Exception.Message
    Write-Host 'Chưa xong. Đang thử lại, cứ để cửa sổ này mở.'
    Start-Sleep -Seconds 15
  }
}
