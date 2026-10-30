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

Trên VPS, hub fb-poller đang chiếm cổng 8088. Coderd dùng cổng 8090 và một token riêng:

```bash
sudo bash deploy/install-systemd.sh
```

File token là `/opt/coderd/coderd.env`, quyền 600. Mở cổng 8090 trên firewall. `CODERD_URL` của PC là `http://IP_VPS:8090`, `WORKER_TOKEN` lấy từ file đó.

Khi VPS đã có Docker và tên miền, `bash deploy/install-vps.sh` dùng Caddy. Không có Docker thì script đó gọi bản systemd.
