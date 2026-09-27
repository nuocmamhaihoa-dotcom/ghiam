# PC Server — băng thông cao (LAN), không nghẽn truyền tải

## Mục tiêu

1. Dùng **1 PC trong mạng local** làm server trung tâm  
2. Mọi PC scanner **ưu tiên LAN** để:
   - tải package / update (zip lớn)  
   - sync comment về server  
3. Tránh nghẽn đường Internet khi nhiều máy cùng cập nhật / đẩy dữ liệu  
4. Server giữ registry agent + DB comment tập trung

## Kiến trúc

```text
[GitHub Release] ──(1 lần / admin)──► upload zip ──► [PC Server :8088]
                                                      │  packages/
                                                      │  server.db (agents + comments)
        ┌────────── LAN (GbE / Wi‑Fi AP) ─────────────┤
        │                                             │
   [PC Scanner 1]  agent: prefer_lan                  │
   [PC Scanner 2]  update + sync-push  ───────────────┘
   [PC Scanner N]
```

Agent mặc định `prefer_lan=true`: lấy manifest từ `http://SERVER:8088/v1/updates/manifest`, tải zip qua LAN (resume Range), sync comment định kỳ.

## Cài PC Server (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Server.ps1 -StartNow
```

Ghi nhớ **Server URL** + **Token** in ra màn hình. Mở firewall TCP `8088` (Private).

Tuỳ chọn:

```powershell
.\pc_agent\windows\Install-Server.ps1 -Port 8088 -Token "doi-secret" -MaxUploadMb 1024 -StartNow
```

## Cài PC Server (Linux)

```bash
chmod +x scripts/install-server.sh
./scripts/install-server.sh
# hoặc:
CONTROL_TOKEN=doi-secret CONTROL_PORT=8088 ./scripts/install-server.sh
./scripts/fb-server-ctl.sh status
```

## Đưa package lên server

**Cách A — copy file**

```text
control_data/packages/fb-poller-windows-v0.2.0.zip
```

**Cách B — upload LAN**

```bash
curl -H "Authorization: Bearer TOKEN" \
  -F file=@fb-poller-windows-v0.2.0.zip \
  http://IP_SERVER:8088/v1/updates/packages/upload
```

Server tự ghi `control_data/update-manifest.json` và phục vụ qua `/v1/updates/manifest`.

## Cài PC Scanner trỏ về server

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ControlUrl "http://IP_SERVER:8088" `
  -ControlToken "TOKEN" `
  -StartNow
```

`config.json` sẽ có:

- `prefer_lan`: true  
- `control_url`: server LAN  
- `sync_comments`: true (đẩy comment về server mỗi ~2 phút)  
- `manifest_url`: vẫn giữ URL GitHub làm **fallback** nếu LAN chết  

## API server (high-bandwidth)

| Method | Path | Việc |
|---|---|---|
| GET | `/health` | Health + mode |
| POST | `/v1/agents/register` | PC kết nối |
| POST | `/v1/agents/heartbeat` | PC báo sống |
| GET | `/v1/agents` | Danh sách PC |
| GET | `/v1/updates/manifest` | Manifest LAN |
| GET | `/v1/updates/packages/{name}` | Tải zip (Accept-Ranges) |
| POST | `/v1/updates/packages/upload` | Admin đẩy zip |
| POST | `/v1/sync/comments` | Bulk sync comment |
| GET | `/v1/server/stats` | Agents / comments / bytes |

Tối ưu LAN: GZip, upload tới 512MB (đổi `CONTROL_MAX_UPLOAD_MB`), uvicorn workers/backlog/keep-alive, SQLite WAL + cache lớn.

## Sync comment thủ công

Trên máy scanner:

```powershell
.\.venv\Scripts\fb-poller.exe sync-push `
  --control-url http://IP_SERVER:8088 `
  --machine-id (Get-Content .\pc_agent\windows\state\machine_id.txt) `
  --token TOKEN
```

## Kiểm tra không nghẽn

1. `GET http://IP:8088/health` → `mode: high-bandwidth-lan-server`  
2. Upload zip qua LAN → xem `approx_mbps` trong response  
3. Agent log: `Downloaded … (~X Mbps)` — kỳ vọng gần tốc độ NIC (100/1000)  
4. `GET /v1/server/stats` → `bytes_total`, `comments`, `agents`  

## Mẹo mạng

- Cắm **Ethernet** server + scanner nếu zip lớn / sync dày  
- Giữ server cùng subnet với agent  
- Không bắt buộc Internet khi đã có package trên server  
- Internet chỉ cần khi admin lấy release mới về rồi upload LAN một lần  

## Gỡ / dừng

Windows: `Unregister-ScheduledTask -TaskName FbPollerServer -Confirm:$false`  
Linux: `./scripts/fb-server-ctl.sh stop`
