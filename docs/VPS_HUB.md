# VPS Hub — máy chủ gốc hiển thị comment + nối PC

VPS đóng vai trò **trung tâm**:

1. Nhận comment đồng bộ từ mọi PC scanner (`/v1/sync/comments`)  
2. Kho kịch bản click tự động trên PC (`/`) — tải phần mềm, lưu / lấy bản ghi. Ghi và Làm lại vẫn chạy trên máy tính.  
3. Phát update package cho agent (`/v1/updates/...`)  
4. Registry PC (`/v1/agents`)

PC chỉ dùng CPU/RAM/proxy local để quét; kết quả đẩy về VPS.

Hỗ trợ Ubuntu/Debian (`apt`) và AlmaLinux/RHEL 8+ (`dnf`).

## Cài trên VPS (đã có sẵn script)

```bash
# Trên máy local: rsync code → /opt/fb-poller
rsync -az --exclude .venv --exclude .git ./ root@VPS_IP:/opt/fb-poller/
ssh root@VPS_IP 'bash /opt/fb-poller/deploy/vps/install-hub.sh'
```

Service systemd: `fb-poller-hub`  
Env/token: `/opt/fb-poller/control_data/server.env` (chmod 600)

## Kết nối PC scanner

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ControlUrl "http://VPS_IP:8088" `
  -ControlToken "TOKEN_TU_server.env" `
  -StartNow
```

Agent sẽ: register → heartbeat → sync-push comment định kỳ → prefer update từ VPS.

## Trang chủ

Mở `http://VPS_IP:8088/` → trang **Click tự động trên PC**: tải `pc-macro.zip`, xem kịch bản đã đưa lên. Ghi phím/chuột trên máy Windows, rồi **Lên VPS**. Kho khách Excel (nếu còn) ở `/khach`, không phải trang chủ.

## API chính

| Path | Việc |
|---|---|
| `/` | Kho kịch bản thao tác PC |
| `/tai/pc-macro.zip` | Tải phần mềm ghi/làm lại trên máy |
| `/v1/macros` | Lưu / liệt kê kịch bản PC |
| `/health` | Health |
| `/v1/comments` | JSON danh sách comment |
| `/v1/sync/comments` | PC đẩy comment |
| `/v1/agents` | PC đã kết nối |
| `/v1/updates/manifest` | Manifest update |

## Bảo mật

- Đổi mật khẩu `root` ngay sau khi nhận VPS (không chia sẻ lại trong chat)  
- Giữ `CONTROL_TOKEN` bí mật; chỉ dán vào agent config  
- Firewall: mở SSH + TCP 8088  
- (Tuỳ chọn) thêm Nginx + HTTPS / Cloudflare sau  
