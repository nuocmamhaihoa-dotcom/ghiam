# VPS Hub — máy chủ gốc hiển thị comment + nối PC

## Địa chỉ và tài khoản

| Mục | Giá trị |
|---|---|
| Link hub | http://222.255.214.202:8088 |
| Trang mở bằng IP | http://222.255.214.202/ |
| Trang chủ | http://222.255.214.202:8088/ |
| Trang iPhone | http://222.255.214.202:8088/iphone |
| Kiểm tra sống | http://222.255.214.202:8088/health |
| Tài khoản SSH | `root` |
| Máy | `root@222.255.214.202` |

Trang web không có tên tài khoản. Mở link hub rồi dán `CONTROL_TOKEN` đang nằm trong `/opt/fb-poller/control_data/server.env`. Không ghi mật khẩu SSH và không ghi token vào tài liệu này.

Hub nghe cổng 8088. Cổng 80 trên cùng máy chuyển tiếp tới cổng đó, nên mở đúng IP cũng vào trang. Nếu hub im, máy tự khởi động lại dịch vụ.

VPS đóng vai trò **trung tâm**:

1. Nhận comment đồng bộ từ mọi PC scanner (`/v1/sync/comments`)  
2. Hiển thị kết quả trên dashboard web (`/`)  
3. Phát update package cho agent (`/v1/updates/...`)  
4. Registry PC (`/v1/agents`)

PC chỉ dùng CPU/RAM/proxy local để quét; kết quả đẩy về VPS.

## Cài trên VPS (đã có sẵn script)

```bash
# Trên máy local: rsync code → /opt/fb-poller
rsync -az --exclude .venv --exclude .git ./ root@222.255.214.202:/opt/fb-poller/
ssh root@222.255.214.202 'bash /opt/fb-poller/deploy/vps/install-hub.sh'
```

Service systemd: `fb-poller-hub`  
Env/token: `/opt/fb-poller/control_data/server.env` (chmod 600)

## Kết nối PC scanner

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ControlUrl "http://222.255.214.202:8088" `
  -ControlToken "TOKEN_TU_server.env" `
  -StartNow
```

Agent sẽ: register → heartbeat → sync-push comment định kỳ → prefer update từ VPS.

## Dashboard

Mở `http://222.255.214.202:8088/` → dán `CONTROL_TOKEN` → xem comments / agents.

## API chính

| Path | Việc |
|---|---|
| `/` | Dashboard |
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
