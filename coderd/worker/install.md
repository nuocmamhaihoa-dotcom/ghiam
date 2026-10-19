# Cài worker trên PC

Linux, user thường, không dùng root:

```bash
bash worker/install-linux.sh
```

Sửa `~/.config/coderd/worker.env`: `CODERD_URL` là địa chỉ VPS, `WORKER_TOKEN` trùng token trên VPS. Rồi:

```bash
systemctl --user restart coderd-worker
```

Windows, trong thư mục `coderd`:

```powershell
powershell -ExecutionPolicy Bypass -File .\worker\install-windows.ps1
```

Điền `worker.env` trước khi đăng nhập lại. Tác vụ `coderd-worker` chạy lúc đăng nhập.

Trên VPS, từ thư mục `coderd`:

```bash
bash deploy/install-vps.sh
```

Đặt `CODERD_SITE` trong `.env` thành tên miền, ví dụ `code.example`, để Caddy lấy HTTPS. Cổng mở 22, 80 và 443.
