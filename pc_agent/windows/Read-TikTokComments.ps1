# Mo Coc Coc tren may Windows nay, mo mot video TikTok, roi luu cac binh luan dang hien.
# Dung dung ho so Coc Coc ban dang xem duoc binh luan. Khong mo trinh duyet ao.
#
#   powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Read-TikTokComments.ps1 -Url "https://www.tiktok.com/@ten/video/123"
#   powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Read-TikTokComments.ps1 -Watch -ControlUrl "http://may:8088" -ControlToken "..."
#
# Coc Coc dang mo se duoc dong roi mo lai bang cung ho so, de phan mem doc duoc cot binh luan.
[CmdletBinding()]
param(
  [string]$Url = "",
  [switch]$Watch,
  [string]$ControlUrl = "",
  [string]$ControlToken = "",
  [int]$Port = 9222,
  [int]$Scrolls = 6,
  [string]$OutFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-CocCoc {
  $roots = @(
    (Join-Path $env:LOCALAPPDATA "CocCoc\Browser\Application"),
    (Join-Path $env:ProgramFiles "CocCoc\Browser\Application"),
    (Join-Path ${env:ProgramFiles(x86)} "CocCoc\Browser\Application")
  )
  foreach ($root in $roots) {
    if (-not $root -or -not (Test-Path $root)) { continue }
    $hit = Get-ChildItem -Path $root -Filter browser.exe -Recurse -ErrorAction SilentlyContinue |
      Sort-Object FullName -Descending |
      Select-Object -First 1
    if ($hit) { return $hit.FullName }
  }
  return $null
}

function Get-CocCocProcesses {
  Get-CimInstance Win32_Process -Filter "Name = 'browser.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -like "*\CocCoc\Browser\*") }
}

function Wait-DebugPort {
  param([int]$Port, [int]$Seconds = 25)
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $ver = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 2
      if ($ver.webSocketDebuggerUrl) { return $ver }
    } catch {
    }
    Start-Sleep -Milliseconds 400
  }
  throw "Coc Coc khong mo cong doc trang. Hay dong het Coc Coc roi chay lai lenh nay."
}

function Get-TikTokPage {
  param([int]$Port, [string]$Url)
  $deadline = (Get-Date).AddSeconds(20)
  while ((Get-Date) -lt $deadline) {
    $list = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 5)
    $page = $list | Where-Object { $_.type -eq "page" -and $_.url -match "tiktok\.com" } | Select-Object -First 1
    if ($page -and $page.webSocketDebuggerUrl) { return $page }
    Start-Sleep -Milliseconds 500
  }
  $list = @(Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 5)
  $page = $list | Where-Object { $_.type -eq "page" -and $_.webSocketDebuggerUrl } | Select-Object -First 1
  if (-not $page) { throw "Khong thay tab nao trong Coc Coc." }
  return $page
}

function Send-WsText {
  param($Socket, [string]$Text)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  $seg = New-Object System.ArraySegment[byte] -ArgumentList $bytes, 0, $bytes.Length
  $null = $Socket.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
}

function Receive-WsText {
  param($Socket, [int]$TimeoutMs = 20000)
  $buf = New-Object byte[] 262144
  $ms = New-Object System.IO.MemoryStream
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
    $seg = New-Object System.ArraySegment[byte] -ArgumentList $buf, 0, $buf.Length
    $task = $Socket.ReceiveAsync($seg, [Threading.CancellationToken]::None)
    if (-not $task.Wait(1000)) { continue }
    $res = $task.Result
    if ($res.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
      throw "Coc Coc dong ket noi doc trang."
    }
    if ($res.Count -gt 0) { $ms.Write($buf, 0, $res.Count) }
    if ($res.EndOfMessage) {
      return [Text.Encoding]::UTF8.GetString($ms.ToArray())
    }
  }
  throw "Het gio khi doi Coc Coc tra loi."
}

function Invoke-Cdp {
  param($Socket, [int]$Id, [string]$Method, $Params)
  $payload = @{ id = $Id; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 30
  Send-WsText -Socket $Socket -Text $payload
  while ($true) {
    $raw = Receive-WsText -Socket $Socket
    if (-not $raw) { throw "Coc Coc tra loi rong." }
    $msg = $raw | ConvertFrom-Json
    if ($null -ne $msg.id -and [int]$msg.id -eq $Id) { return $msg }
  }
}

function Start-CommentWatch {
  if (-not $ControlUrl -or -not $ControlToken) {
    $cfgPath = Join-Path $PSScriptRoot "config.json"
    if (Test-Path $cfgPath) {
      $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if (-not $ControlUrl -and $cfg.control_url) { $script:ControlUrl = [string]$cfg.control_url }
      if (-not $ControlToken -and $cfg.control_token) { $script:ControlToken = [string]$cfg.control_token }
    }
  }
  if (-not $ControlUrl -or -not $ControlToken) {
    throw "Can -ControlUrl va -ControlToken, hoac file config.json nam canh script."
  }
  $headers = @{ Authorization = "Bearer $ControlToken" }
  $base = $ControlUrl.TrimEnd("/")
  Write-Host "Dang cho video moi tu $base. Giu cua so nay mo."
  while ($true) {
    try {
      $claimed = Invoke-RestMethod -Uri ($base + "/v1/tiktok/jobs/claim") -Method POST -Headers $headers -TimeoutSec 30
      if (-not $claimed.job) {
        Start-Sleep -Seconds 4
        continue
      }
      $job = $claimed.job
      $out = Join-Path $env:TEMP ("ghiam-tiktok-job-" + $job.id + ".json")
      Write-Host "Nhan video $($job.id): $($job.url)"
      $arg = "-ExecutionPolicy Bypass -File `"$PSCommandPath`" -Url `"$($job.url)`" -Port $Port -Scrolls $Scrolls -OutFile `"$out`" -ControlUrl `"$ControlUrl`" -ControlToken `"$ControlToken`""
      $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $arg -Wait -PassThru -NoNewWindow
      $status = "error"
      $detail = "Coc Coc khong chay xong"
      $count = 0
      if (Test-Path $out) {
        $saved = Get-Content $out -Raw -Encoding UTF8 | ConvertFrom-Json
        $count = @($saved.comments).Count
      }
      if ($proc.ExitCode -eq 0) {
        $status = "done"
        $detail = "Da luu $count binh luan"
      } elseif ($proc.ExitCode -eq 2) {
        $status = "empty"
        $detail = "Khong thay binh luan tren Coc Coc"
      } else {
        $detail = "Ma loi $($proc.ExitCode)"
      }
      $finish = @{ status = $status; detail = $detail; comment_count = $count } | ConvertTo-Json -Compress
      Invoke-RestMethod -Uri ($base + "/v1/tiktok/jobs/$($job.id)/finish") -Method POST -Headers $headers -Body $finish -ContentType "application/json; charset=utf-8" -TimeoutSec 30 | Out-Null
      Write-Host "Xong video $($job.id): $detail"
    } catch {
      Write-Host "Cho lai: $($_.Exception.Message)"
      Start-Sleep -Seconds 5
    }
  }
}

if ($Watch) {
  Start-CommentWatch
  return
}

$Url = $Url.Trim()
if (-not $Url) { throw "Can -Url, hoac chay -Watch de lay link tu trang web." }
if ($Url -notmatch '^https://([a-z0-9-]+\.)*tiktok\.com/') {
  throw "Can dung link https cua tiktok.com. Vi du: https://www.tiktok.com/@ten/video/123"
}

$exe = Find-CocCoc
if (-not $exe) {
  throw "Khong thay Coc Coc. Cai Coc Coc tren may nay roi chay lai."
}
$userData = Join-Path $env:LOCALAPPDATA "CocCoc\Browser\User Data"
if (-not (Test-Path $userData)) {
  throw "Khong thay ho so Coc Coc tai $userData. Hay mo Coc Coc mot lan va xem duoc binh luan TikTok, roi chay lai."
}

Write-Host "Buoc 1: Tim thay Coc Coc: $exe"
$running = @(Get-CocCocProcesses)
if ($running) {
  Write-Host "Buoc 2: Dong Coc Coc dang mo, roi mo lai cung ho so."
  foreach ($proc in $running) {
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Seconds 2
} else {
  Write-Host "Buoc 2: Coc Coc dang tat. Mo moi bang ho so cu."
}

$args = @(
  "--remote-debugging-port=$Port",
  "--remote-debugging-address=127.0.0.1",
  "--remote-allow-origins=*",
  "--user-data-dir=$userData",
  "--no-first-run",
  "--no-default-browser-check",
  "--lang=vi",
  $Url
)
Write-Host "Buoc 3: Mo Coc Coc bang ho so dang xem duoc binh luan."
Start-Process -FilePath $exe -ArgumentList $args | Out-Null
Wait-DebugPort -Port $Port | Out-Null
$page = Get-TikTokPage -Port $Port -Url $Url

$ws = [System.Net.WebSockets.ClientWebSocket]::new()
$ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null

$readJs = @'
() => {
  const nodes = [...document.querySelectorAll('[data-e2e="comment-level-1"]')];
  const comments = [];
  const seen = new Set();
  for (const el of nodes) {
    const userEl = el.querySelector('[data-e2e="comment-username-1"], [data-e2e^="comment-username"]');
    const textEl = el.querySelector('[data-e2e="comment-text"]');
    const author = userEl ? userEl.innerText.trim() : "";
    let text = textEl ? textEl.innerText.trim() : "";
    if (!text) {
      const lines = (el.innerText || "").split("\n").map((s) => s.trim()).filter(Boolean);
      const body = lines.filter((line) => line !== author && !/^(trả lời|xem \d+)/i.test(line));
      text = body.join(" ").trim();
    }
    if (!text) continue;
    const link = el.querySelector('a[href*="comment"]');
    const href = link ? link.getAttribute("href") || "" : "";
    const idMatch = href.match(/(\d{8,})/);
    const commentId = idMatch ? idMatch[1] : (author + "|" + text).slice(0, 180);
    if (seen.has(commentId)) continue;
    seen.add(commentId);
    comments.push({ comment_id: commentId, author_name: author, text: text });
  }
  const body = document.body ? document.body.innerText : "";
  const gate = /When's your birthday|ngày sinh|Log in to TikTok|Đăng nhập để bình luận|Kéo thanh trượt/i.test(body);
  return { title: document.title || "", url: location.href, count: comments.length, comments: comments, gate: gate };
}
'@

$scrollJs = @'
() => {
  const node = document.querySelector('[data-e2e="comment-level-1"]');
  let el = node ? node.parentElement : null;
  for (let i = 0; i < 6 && el; i++) {
    if (el.scrollHeight > el.clientHeight + 40) {
      el.scrollTop = el.scrollHeight;
      return true;
    }
    el = el.parentElement;
  }
  return false;
}
'@

$openJs = @'
() => {
  const n = document.querySelectorAll('[data-e2e="comment-level-1"]').length;
  if (n > 0) return "ready";
  const body = document.body ? document.body.innerText : "";
  if (/When's your birthday|ngày sinh|Kéo thanh trượt/i.test(body)) return "gate";
  const btn = document.querySelector('[data-e2e="comment-icon"]');
  if (btn) { btn.click(); return "clicked"; }
  return "wait";
}
'@

Write-Host "Buoc 4: Mo dung link video."
$null = Invoke-Cdp -Socket $ws -Id 1 -Method "Runtime.enable" -Params @{}
$null = Invoke-Cdp -Socket $ws -Id 2 -Method "Page.navigate" -Params @{ url = $Url }
Start-Sleep -Seconds 4

Write-Host "Buoc 5: Mo cot binh luan va doi cac dong chu hien ra."
$nextId = 3
$opened = "wait"
for ($try = 0; $try -lt 12; $try++) {
  $openedResp = Invoke-Cdp -Socket $ws -Id $nextId -Method "Runtime.evaluate" -Params @{
    expression = $openJs
    returnByValue = $true
  }
  $nextId++
  if ($openedResp.result -and $openedResp.result.result) {
    $opened = [string]$openedResp.result.result.value
  }
  if ($opened -eq "ready" -or $opened -eq "gate") { break }
  Start-Sleep -Seconds 1
}

$merged = @{}
$last = $null
Write-Host "Buoc 6: Keo cot binh luan de lay them dong dang hien."
for ($i = 0; $i -le $Scrolls; $i++) {
  $resp = Invoke-Cdp -Socket $ws -Id $nextId -Method "Runtime.evaluate" -Params @{
    expression = $readJs
    returnByValue = $true
    awaitPromise = $false
  }
  $nextId++
  if (-not $resp.result -or -not $resp.result.result) {
    throw "Coc Coc khong tra du lieu trang."
  }
  $value = $resp.result.result.value
  $last = $value
  if ($value -and $value.comments) {
    foreach ($c in @($value.comments)) {
      $merged[$c.comment_id] = $c
    }
  }
  if ($i -lt $Scrolls) {
    $null = Invoke-Cdp -Socket $ws -Id $nextId -Method "Runtime.evaluate" -Params @{
      expression = $scrollJs
      returnByValue = $true
    }
    $nextId++
    Start-Sleep -Milliseconds 1500
  }
}
$ws.Dispose()

$comments = @($merged.Values)
if (-not $OutFile) {
  $dir = Join-Path $env:USERPROFILE "ghiam-tiktok-comments"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $safe = [regex]::Replace($Url, "[^0-9A-Za-z]+", "_")
  if ($safe.Length -gt 80) { $safe = $safe.Substring($safe.Length - 80) }
  $OutFile = Join-Path $dir ($safe + ".json")
}

$result = [ordered]@{
  url = $Url
  title = $(if ($last) { $last.title } else { "" })
  page = $(if ($last) { $last.url } else { "" })
  saved_at = (Get-Date).ToUniversalTime().ToString("o")
  count = $comments.Count
  comments = $comments
}
($result | ConvertTo-Json -Depth 8) | Set-Content -Path $OutFile -Encoding UTF8
Write-Host "Buoc 7: Da luu $($comments.Count) binh luan vao $OutFile"

$cfgPath = Join-Path $PSScriptRoot "config.json"
$hubUrl = $ControlUrl
$hubToken = $ControlToken
if ((-not $hubUrl -or -not $hubToken) -and (Test-Path $cfgPath)) {
  $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if (-not $hubUrl -and $cfg.control_url) { $hubUrl = [string]$cfg.control_url }
  if (-not $hubToken -and $cfg.control_token) { $hubToken = [string]$cfg.control_token }
}
if (($comments.Count -gt 0) -and $hubUrl -and $hubToken) {
    $machine = $env:COMPUTERNAME
    $idFile = Join-Path $PSScriptRoot "state\machine_id.txt"
    if (Test-Path $idFile) { $machine = (Get-Content $idFile -Raw).Trim() }
    $body = @{
      machine_id = $machine
      comments = @(
        $comments | ForEach-Object {
          @{
            comment_id = $_.comment_id
            post_url = $Url
            post_id = ""
            text = $_.text
            author_name = $_.author_name
            author_id = ""
          }
        }
      )
    }
    $headers = @{ Authorization = "Bearer $hubToken" }
    $syncUrl = ($hubUrl.TrimEnd("/")) + "/v1/sync/comments"
    try {
      $synced = Invoke-RestMethod -Uri $syncUrl -Method POST -Headers $headers -Body ($body | ConvertTo-Json -Depth 8 -Compress) -ContentType "application/json; charset=utf-8" -TimeoutSec 60
      Write-Host "Buoc 8: Da day len hub: nhan $($synced.received), moi $($synced.inserted_new)"
    } catch {
      Write-Host "Luu file xong. Day len hub chua duoc: $($_.Exception.Message)"
    }
}

if ($comments.Count -eq 0) {
  if ($last -and $last.gate) {
    Write-Host "Coc Coc mo duoc video nhung TikTok chua ve cot binh luan (dang nhap hoac ngay sinh)."
  } else {
    Write-Host "Coc Coc mo duoc trang nhung khong thay dong binh luan nao."
  }
  exit 2
}
exit 0
