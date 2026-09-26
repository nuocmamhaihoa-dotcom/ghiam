# Cài đặt fb-poller trên PC để quét comment (PA1)

Phần mềm chạy **trên máy bạn**, dùng CPU/RAM/proxy của PC — không phụ thuộc máy cloud.

## Yêu cầu

- Linux (Ubuntu 22.04+/Debian) **hoặc** Windows 10/11
- Python 3.11+
- ~8GB+ RAM trống (khuyến nghị 32GB+ nếu `WORKERS≥10`)
- Danh sách URL bài công khai + proxy tĩnh (khuyến nghị)

## Linux — cài 1 lệnh

Trong thư mục project:

```bash
chmod +x install.sh scripts/*.sh
./install.sh          # tự nhận profile theo số CPU/RAM
# hoặc ép profile:
./install.sh pc20     # 14 workers
./install.sh pc12     # 6 workers
```

Installer sẽ:
1. Cài dependency hệ thống + Python venv  
2. Cài Playwright Chromium  
3. Tạo `.env` khớp tài nguyên máy  
4. Tạo `data/posts.txt`, `data/proxies_static.txt`  
5. Init database SQLite local  

### Cấu hình lại workers (tuỳ chọn)

```bash
./scripts/configure_pc.sh
./scripts/fb-poller-ctl.sh doctor
```

### Nạp URL + proxy

```bash
nano data/posts.txt            # 1 permalink / dòng
nano data/proxies_static.txt   # 1 proxy / dòng (http://user:pass@host:port)
./scripts/fb-poller-ctl.sh import
./scripts/fb-poller-ctl.sh probe 10 2
```

### Chạy bằng tài nguyên PC

```bash
./scripts/fb-poller-ctl.sh start     # chạy nền
./scripts/fb-poller-ctl.sh status
./scripts/fb-poller-ctl.sh logs
./scripts/fb-poller-ctl.sh stop
```

### Tự chạy khi bật máy (systemd user)

```bash
./scripts/fb-poller-ctl.sh install-service
systemctl --user status fb-poller
```

Gỡ:

```bash
./scripts/fb-poller-ctl.sh uninstall-service
```

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
# sửa data\posts.txt + data\proxies_static.txt
.\scripts\fb-poller-ctl.ps1 import
.\scripts\fb-poller-ctl.ps1 start
.\scripts\fb-poller-ctl.ps1 status
```

Hoặc:

```powershell
.\.venv\Scripts\Activate.ps1
fb-poller run
```

## Phân bổ 2 PC của bạn

| Máy | Lệnh cài | WORKERS gợi ý |
|---|---|---|
| 20 nhân / 64GB | `./install.sh pc20` | 14 |
| 12 nhân / 64GB | `./install.sh pc12` | 6 |

- Mỗi máy dùng `.env` riêng (`WORKER_ID` khác nhau).  
- Muốn 2 máy chung 1 DB: đưa Postgres lên PC12, cùng `DATABASE_URL` trên cả 2.  
- Chia proxy tĩnh: PC20 ~14 IP, PC12 ~6 IP (không trùng nếu có thể).

## Kiểm tra sức khoẻ

```bash
./scripts/fb-poller-ctl.sh doctor
fb-poller kpi
```

Chỉ hạ `HOT_INTERVAL_SEC=30` khi `fb-poller kpi` báo `ready_for_30s=true`.

## Luồng sử dụng hàng ngày

```text
import URL/proxy  →  doctor  →  start  →  status/logs  →  kpi
```

Log mặc định: `logs/fb-poller.out`  
DB mặc định: `data/fb_poller.db` (trên ổ đĩa PC)
