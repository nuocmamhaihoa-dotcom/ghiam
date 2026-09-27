# Agent Windows — bật là kết nối, tự cập nhật, nhiều PC không giới hạn

## Mục tiêu

1. Cài **một lần** trên mỗi PC Windows  
2. Bật máy / đăng nhập là **tự kết nối** và **tự chạy** bộ quét comment  
3. Khi bạn **phát hành bản mới**, mọi PC **tự tải và nâng cấp** (agent + poller) — không cần cài lại  
4. Cài được **không giới hạn số PC** (mỗi máy có `machine_id` riêng)

## Kiến trúc

```text
[Bạn phát hành Release GitHub]
        │ update-manifest.json + zip
        ▼
[PC1 Agent]──┐
[PC2 Agent]──┼──► (tuỳ chọn) Control Plane  :8088
[PC N Agent]─┘         register + heartbeat
        │
        ▼
   fb-poller run  (quét comment bằng CPU/RAM máy đó)
```

Giữ nguyên khi update: `data/`, `.env`, `logs/`, `pc_agent/windows/state/`, `config.json`.

## Cài một lần trên mỗi PC

1. Cài [Python 3.11+](https://www.python.org/downloads/) (tick **Add python.exe to PATH**).  
2. Copy/giải nén project vào ổ đĩa local, ví dụ `C:\fb-poller`.  
3. Mở PowerShell **trong thư mục đó**:

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 -StartNow
```

Tuỳ chọn kết nối control plane + kênh update:

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ManifestUrl "https://github.com/<org>/<repo>/releases/latest/download/update-manifest.json" `
  -ControlUrl "http://IP_MAY_CHU:8088" `
  -ControlToken "secret-neu-co" `
  -Workers 14 `
  -StartNow
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

Chạy ngay vòng kết nối + update + start poller:

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\FbPollerAgent.ps1 -Once
```

## “Bật là tự kết nối” làm gì?

Khi agent chạy, nó lần lượt:

1. Tạo/đọc `machine_id` (không trùng giữa các PC)  
2. `POST /v1/agents/register` tới control plane (nếu có `control_url`)  
3. Kiểm tra `manifest_url` → tự update nếu có bản mới  
4. Đảm bảo `fb-poller run` đang chạy  
5. Gửi heartbeat định kỳ  

Không có control plane vẫn chạy được (offline / update-only).

## Nâng cấp phần mềm sau này (không cài lại PC)

### Cách khuyến nghị: GitHub Release

1. Tăng version:
   - `VERSION` (poller)
   - `pc_agent/windows/VERSION` (agent)
2. Tag: `git tag v0.2.0 && git push origin v0.2.0`
3. Workflow `.github/workflows/release-windows.yml` tạo:
   - `fb-poller-windows-v0.2.0.zip`
   - `update-manifest.json` (kèm sha256)
4. Trên mỗi PC, agent tự tải zip → thay code → giữ data → restart poller

Trỏ `manifest_url` trong `pc_agent/windows/config.json` tới:

```text
https://github.com/<org>/<repo>/releases/latest/download/update-manifest.json
```

### Ép update thủ công trên 1 PC

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Update-FromManifest.ps1 -Force
```

## Control plane (tuỳ chọn, không giới hạn PC)

Chạy trên 1 máy chủ / VPS / PC12:

```bash
pip install fastapi uvicorn
export CONTROL_TOKEN=doi-secret
uvicorn control_plane.app:app --host 0.0.0.0 --port 8088
```

API:

| Method | Path | Việc |
|---|---|---|
| POST | `/v1/agents/register` | PC kết nối lần đầu / lại |
| POST | `/v1/agents/heartbeat` | PC báo sống + poller running |
| GET | `/v1/agents` | Liệt kê mọi PC đã kết nối |
| GET | `/health` | Healthcheck |

Mỗi PC chỉ cần `machine_id` — không có giới hạn license trong phần mềm.

## Gỡ agent

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Uninstall-Agent.ps1
```

Dữ liệu `data/` (comment đã quét) được giữ.

## Kiểm tra nhanh

| Việc | Cách |
|---|---|
| Task có chưa | `Get-ScheduledTask -TaskName FbPollerAgent` |
| Log agent | `logs\agent.log` |
| Log poller | `logs\fb-poller.out` |
| PC đã lên control | `GET http://server:8088/v1/agents` |
| Version local | `Get-Content VERSION; Get-Content pc_agent\windows\VERSION` |

## Nhiều PC

- Cài `Install-Agent.ps1` trên mỗi máy (không giới hạn)  
- `WORKER_ID` mặc định = `TENMAY-1`  
- Chia proxy tĩnh theo máy để tránh trùng session  
- Nếu dùng chung DB: đổi `DATABASE_URL` trong `.env` sang Postgres dùng chung  
