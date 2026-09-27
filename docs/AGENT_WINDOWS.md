# Agent Windows — bật là kết nối, tự cập nhật, nhiều PC không giới hạn

## Mục tiêu

1. Cài **một lần** trên mỗi PC Windows  
2. Bật máy / đăng nhập là **tự kết nối** và **tự chạy** bộ quét comment  
3. Khi bạn **phát hành bản mới**, mọi PC **tự tải và nâng cấp** (agent + poller) — không cần cài lại  
4. Cài được **không giới hạn số PC** (mỗi máy có `machine_id` riêng)  
5. Ưu tiên **LAN server băng thông cao** để update/sync không nghẽn Internet — xem [`SERVER_PC.md`](SERVER_PC.md)

## Kiến trúc

```text
[Bạn phát hành Release / upload zip lên PC Server]
        │
        ▼
[PC Server LAN :8088]  ◄── prefer_lan: manifest + packages + comment sync
        ▲
[PC1 Agent]──┐
[PC2 Agent]──┼── register / heartbeat / sync-push
[PC N Agent]─┘
        │
        ▼
   fb-poller run  (quét comment bằng CPU/RAM máy đó)
```

Giữ nguyên khi update: `data/`, `.env`, `logs/`, `control_data/`, `pc_agent/windows/state/`, `config.json`.

## Cài một lần trên mỗi PC

1. Cài [Python 3.11+](https://www.python.org/downloads/) (tick **Add python.exe to PATH**).  
2. Copy/giải nén project vào ổ đĩa local, ví dụ `C:\fb-poller`.  
3. Mở PowerShell **trong thư mục đó**:

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 -StartNow
```

**Khuyến nghị** — trỏ về PC server LAN:

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ControlUrl "http://IP_MAY_CHU:8088" `
  -ControlToken "secret-neu-co" `
  -Workers 14 `
  -StartNow
```

Tuỳ chọn thêm fallback Internet:

```powershell
  -ManifestUrl "https://github.com/<org>/<repo>/releases/latest/download/update-manifest.json"
```

4. Dán URL/proxy:

```text
data\posts.txt
data\proxies_static.txt
```

5. Import:

```powershell
.\.venv\Scripts\fb-poller.exe import-urls .\data\posts.txt
.\.venv\Scripts\fb-poller.exe import-proxies
.\.venv\Scripts\fb-poller.exe rebalance-hot
```

Agent đã đăng ký Task Scheduler tên **`FbPollerAgent`** — lần đăng nhập sau sẽ tự chạy.

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\FbPollerAgent.ps1 -Once
```

## “Bật là tự kết nối” làm gì?

1. Tạo/đọc `machine_id`  
2. `POST /v1/agents/register` tới LAN server (kèm link speed NIC)  
3. `prefer_lan` → lấy manifest từ server; tải zip LAN + **resume**; fallback Internet nếu LAN lỗi  
4. Đảm bảo `fb-poller run` đang chạy  
5. Heartbeat định kỳ  
6. `sync-push` comment về server (mặc định mỗi 2 phút)  

## Nâng cấp phần mềm (không cài lại PC)

### Cách khuyến nghị: PC Server LAN

1. Build/release zip (hoặc lấy từ GitHub Release)  
2. Upload lên server: xem [`SERVER_PC.md`](SERVER_PC.md)  
3. Agent mọi PC tự tải qua LAN (Gbps), không nghẽn Internet  

### Cách phụ: GitHub Release trực tiếp

1. Tăng `VERSION` + `pc_agent/windows/VERSION`  
2. Tag `v*` → workflow tạo zip + `update-manifest.json`  
3. `manifest_url` trỏ Release; chỉ dùng khi không có LAN server  

### Ép update thủ công

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Update-FromManifest.ps1 -Force
```

## Control plane / Server

Chi tiết đầy đủ: [`SERVER_PC.md`](SERVER_PC.md).

```powershell
# Windows
.\pc_agent\windows\Install-Server.ps1 -StartNow

# Linux
./scripts/install-server.sh
```

## Gỡ agent

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Uninstall-Agent.ps1
```

Dữ liệu `data/` được giữ.

## Kiểm tra nhanh

| Việc | Cách |
|---|---|
| Task agent | `Get-ScheduledTask -TaskName FbPollerAgent` |
| Task server | `Get-ScheduledTask -TaskName FbPollerServer` |
| Log agent | `logs\agent.log` |
| Log poller | `logs\fb-poller.out` |
| Log server | `logs\server.out` |
| PC đã lên server | `GET http://server:8088/v1/agents` |
| Stats truyền tải | `GET http://server:8088/v1/server/stats` |

## Nhiều PC

- Cài `Install-Agent.ps1` trên mỗi máy (không giới hạn)  
- `WORKER_ID` mặc định = `TENMAY-1`  
- Chia proxy tĩnh theo máy  
- Sync comment về 1 PC server qua LAN — không cần mỗi máy đẩy Internet  
