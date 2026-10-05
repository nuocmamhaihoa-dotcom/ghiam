# VPS Hub — máy chủ gốc hiển thị comment + nối PC

## Địa chỉ và tài khoản

| Mục | Giá trị |
|---|---|
| Link hub | http://14.225.224.16:8088 |
| Trang mở bằng IP | http://14.225.224.16/ |
| Trang chủ | http://14.225.224.16:8088/ |
| Trang iPhone | http://14.225.224.16:8088/iphone |
| Kiểm tra sống | http://14.225.224.16:8088/health |
| Tài khoản SSH | `root` |
| Máy | `root@14.225.224.16` |
| Nhà cung cấp | Vietnix |

Trang web không có tên tài khoản. Mở link hub rồi dán `CONTROL_TOKEN` đang nằm trong `/opt/fb-poller/control_data/server.env`. Không ghi mật khẩu SSH và không ghi token vào tài liệu này.

Hub nghe cổng 8088. Cổng 80 trên cùng máy chuyển tiếp tới cổng đó, nên mở đúng IP cũng vào trang. Nếu hub im, máy tự khởi động lại dịch vụ.

Kết quả đã quét nằm ở mục **Kết quả quét** trên trang chủ và mục **Kết quả đã quét** trên trang iPhone. Lọc theo ngày hoặc theo tên máy. Mỗi kết quả được ghi ba hướng, chỉ thêm, không sửa, không xoá:

| Hướng | Chỗ trên VPS |
|---|---|
| Sổ trong cơ sở chính | `/opt/fb-poller/control_data/server.db` |
| Cơ sở SQLite riêng | `/var/lib/fb-poller/scan_vault/scan_facts.db` |
| Nhật ký chữ | `/var/backups/fb-poller/scan_facts.jsonl` |

Mất một hoặc hai hướng thì lần mở máy dựng lại từ hướng còn. Ba hướng này cùng một ổ đĩa của VPS; mất cả ổ thì không còn bản nào trên máy đó.

Nút **Tải toàn bộ** trong mục kết quả đã quét gửi hết sổ về máy dưới dạng file CSV. Lần tải cần mật khẩu riêng, ngoài token của trang. Bản lưu trên máy chủ là chuỗi scrypt trong `CONTROL_SCAN_EXPORT_PASSWORD`. Tài liệu này không ghi mật khẩu gốc.

VPS đóng vai trò **trung tâm**:

1. Nhận comment đồng bộ từ mọi PC scanner (`/v1/sync/comments`)  
2. Hiển thị kết quả trên dashboard web (`/`)  
3. Phát update package cho agent (`/v1/updates/...`)  
4. Registry PC (`/v1/agents`)

PC chỉ dùng CPU/RAM/proxy local để quét; kết quả đẩy về VPS.

Phần phân tích và đọc video trên VPS dùng 90% CPU và 90% RAM suốt thời gian đang đọc. PC đang nối dùng 80% CPU và 80% RAM của máy đó suốt thời gian nối, không tăng khi máy rảnh. Phần còn lại để trang hub và nhịp nối vẫn trả lời.

## Cài trên VPS (đã có sẵn script)

```bash
# Trên máy local: rsync code → /opt/fb-poller
rsync -az --exclude .venv --exclude .git ./ root@14.225.224.16:/opt/fb-poller/
ssh root@14.225.224.16 'bash /opt/fb-poller/deploy/vps/install-hub.sh'
```

Service systemd: `fb-poller-hub`  
Env/token: `/opt/fb-poller/control_data/server.env` (chmod 600)

## Kết nối PC scanner

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 `
  -ControlUrl "http://14.225.224.16:8088" `
  -ControlToken "TOKEN_TU_server.env" `
  -StartNow
```

Agent sẽ: register → heartbeat → sync-push comment định kỳ → prefer update từ VPS.

## Dashboard

Mở `http://14.225.224.16:8088/` → dán `CONTROL_TOKEN` → xem comments / agents.

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
