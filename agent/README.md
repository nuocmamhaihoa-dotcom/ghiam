# Agent CommentScope (chạy trên máy PC)

Agent là chương trình chạy trên các máy PC làm việc (worker). Agent tự gọi lên VPS qua HTTPS nên máy ở sau NAT hay dùng
mạng 4G vẫn chạy được, không cần IP tĩnh hay mở cổng.

Hiện có năm lệnh:

| Lệnh | Việc làm |
| --- | --- |
| `ping` | Kiểm tra kết nối và token tới VPS |
| `check` | Thuê một proxy từ kho trên VPS, kiểm tra IP ra qua proxy đó rồi trả lại kèm kết quả |
| `open URL...` | Thuê một proxy và mở Chromium qua proxy đó, mở các trang được chỉ định |
| `scrape URL` | Thuê một proxy và đọc comment công khai của một permalink Facebook, in ra màn hình |
| `run` | Nhận job từ VPS và đọc comment liên tục, gửi kết quả về; bản chưa gửi được lưu ở `commentscope-data/outbox.jsonl` |

`scrape` và `run` chỉ đọc comment đang hiện trên bài viết công khai. Agent không đăng nhập, không điền mật khẩu và không
giải captcha. Trang yêu cầu đăng nhập được báo là bị chặn để VPS thử proxy khác.

## Yêu cầu

- Windows 10/11, macOS hoặc Linux, Python 3.11 trở lên.
- Kết nối được tới VPS qua HTTPS.
- Một token trong biến `AGENT_TOKENS` trên VPS (nên cấp mỗi máy một token riêng).
- Vài trăm MB dung lượng trống cho Chromium do Playwright tải về.

## Cài đặt

### Windows (PowerShell)

Cài Python từ [python.org](https://www.python.org/downloads/) (chọn "Add python.exe to PATH"), rồi trong thư mục `agent`:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install .
python -m playwright install chromium
```

Nếu PowerShell báo không được chạy script khi kích hoạt môi trường ảo, chạy một lần
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` rồi thử lại, hoặc dùng Command Prompt với `.venv\Scripts\activate.bat`.

### Linux và macOS

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install .
python -m playwright install chromium
```

Trên Linux, cài thêm thư viện hệ thống mà Chromium cần (lệnh gọi trình quản lý gói của hệ điều hành, có thể hỏi mật khẩu
sudo):

```bash
python -m playwright install-deps chromium
```

Sau khi cài, lệnh `commentscope-agent` dùng được mỗi khi môi trường ảo đang được kích hoạt. Chạy
`python -m commentscope_agent` trong thư mục `agent` cũng tương đương.

**Cập nhật agent**: lấy mã nguồn mới, chạy lại `pip install .` trong môi trường ảo, rồi chạy lại
`python -m playwright install chromium` (mỗi phiên bản Playwright dùng một bản Chromium riêng).

## Cấu hình

Agent đọc cấu hình từ ba nguồn, nguồn sau ghi đè nguồn trước: file `config.json` < biến môi trường `COMMENTSCOPE_*` <
tham số dòng lệnh.

### File `config.json`

Mặc định agent đọc `config.json` ở **thư mục đang đứng khi chạy lệnh**; dùng `--config đường-dẫn` để chỉ file khác. Chép
file mẫu rồi sửa:

```bash
cp config.example.json config.json
```

```json
{
  "server_url": "https://scope.example.com",
  "token": "dan-mot-token-trong-AGENT_TOKENS-tren-VPS",
  "worker_id": "pc-van-phong-01",
  "pool": "vn-static",
  "kind": null,
  "lease_ttl_sec": 600,
  "lease_wait_sec": 120,
  "request_timeout_sec": 20
}
```

File này chứa token nên đừng chia sẻ hay commit (`agent/config.json` đã có trong `.gitignore`); trên Linux/macOS nên
`chmod 600 config.json`.

| Khoá trong file | Biến môi trường | Tham số | Mặc định | Ý nghĩa |
| --- | --- | --- | --- | --- |
| `server_url` | `COMMENTSCOPE_SERVER_URL` | `--server` | (bắt buộc) | Địa chỉ VPS, chỉ cần `https://tên-miền` |
| `token` | `COMMENTSCOPE_AGENT_TOKEN` | không có | (bắt buộc) | Một token trong `AGENT_TOKENS` trên VPS |
| `worker_id` | `COMMENTSCOPE_WORKER_ID` | `--worker-id` | tên máy | Tên máy PC, được ghi vào lượt thuê proxy trên VPS (tối đa 128 ký tự) |
| `pool` | `COMMENTSCOPE_POOL` | `--pool` | mọi pool | Chỉ thuê proxy trong pool này |
| `kind` | `COMMENTSCOPE_KIND` | `--kind` | mọi loại | `static` (proxy tĩnh) hoặc `rotating` (proxy 4G xoay) |
| `lease_ttl_sec` | | `--ttl` | `600` | Thời hạn mỗi lượt thuê, agent tự gia hạn (30–86400; VPS cắt về tối đa `PROXY_LEASE_MAX_TTL_SEC`, mặc định 3600) |
| `lease_wait_sec` | | `--wait` | `120` | Chờ tối đa bao nhiêu giây khi VPS chưa có proxy rảnh (0 = không chờ) |
| `request_timeout_sec` | | | `20` | Thời gian chờ mỗi request tới VPS, khi kết nối tới proxy và khi kiểm tra IP |
| `check_url` | `COMMENTSCOPE_CHECK_URL` | `--check-url` | theo VPS | Trang kiểm tra IP ra; mặc định dùng `PROXY_CHECK_URLS` của VPS |

Token chỉ nhận qua file cấu hình hoặc biến môi trường, không có tham số dòng lệnh: tham số hiện trong danh sách tiến
trình của máy, người dùng khác trên máy có thể đọc được.

### Biến môi trường

PowerShell (chỉ có hiệu lực trong cửa sổ đang mở):

```powershell
$env:COMMENTSCOPE_SERVER_URL = "https://scope.example.com"
$env:COMMENTSCOPE_AGENT_TOKEN = Read-Host "Token agent"
```

Linux/macOS (`read -s` để token không hiện trên màn hình và không nằm trong lịch sử lệnh):

```bash
export COMMENTSCOPE_SERVER_URL=https://scope.example.com
read -rsp "Token agent: " COMMENTSCOPE_AGENT_TOKEN && export COMMENTSCOPE_AGENT_TOKEN
```

## Cách dùng

```bash
commentscope-agent ping
commentscope-agent check --pool vn-static
commentscope-agent check --kind rotating --rotate
commentscope-agent open https://example.com --pool vn-static
commentscope-agent open example.com example.org --headless --screenshot-dir anh-chup
commentscope-agent open https://example.com --keep-open
commentscope-agent scrape https://www.facebook.com/trang/posts/1234567890 --max-comments 100
commentscope-agent run --capacity 1
```

`scrape` in từng comment (tác giả, nội dung, lượt thích). Thêm `--json` để nhận một object gồm `comments`. `run` chạy
tới khi nhấn Ctrl+C; `--show` mở cửa sổ Chromium thay vì chạy ẩn. `--capacity` từ 1 đến 4 (mặc định 1).

Ví dụ dưới đây chạy với [mạng proxy giả lập](../README.md#3-thử-với-mạng-proxy-giả-lập), nên proxy có địa chỉ
`127.0.0.1`.

`ping`:

```
Kết nối tới http://127.0.0.1:8000 thành công sau 18 ms: token hợp lệ (agent-token-1), máy PC 'pc-van-phong-01'
Phiên bản server 0.1.0, agent 0.1.0
Trang kiểm tra IP: http://ip-check.sim/json
```

`open` (proxy 4G đổi IP bằng session):

```
Đã thuê proxy #10 http://127.0.0.1:35345 (pool 4g-gateway), agent tự gia hạn tới khi trả
Đang kiểm tra IP ra của proxy...
IP ra của proxy: 198.18.134.173 (VN), ip-check.sim trả lời sau 2 ms
Đang mở http://ip-check.sim/json ...
  HTTP 200, 17 ms
Kết quả: proxy dùng tốt (Mở được 2 trang qua proxy). Đã trả proxy cho VPS
```

### Tham số

Dùng cho mọi lệnh:

| Tham số | Ý nghĩa |
| --- | --- |
| `--config FILE` | File cấu hình JSON (mặc định `config.json` ở thư mục hiện tại) |
| `--server URL` | Địa chỉ VPS |
| `--worker-id TÊN` | Tên máy PC (mặc định: tên máy) |
| `--json` | In kết quả dạng JSON ra stdout, thông báo ra stderr |

Dùng cho `check` và `open`:

| Tham số | Ý nghĩa |
| --- | --- |
| `--pool TÊN` | Chỉ thuê proxy trong pool này |
| `--kind static\|rotating` | Chỉ thuê proxy tĩnh hoặc proxy 4G xoay |
| `--wait GIÂY` | Chờ tối đa khi chưa có proxy rảnh (mặc định 120) |
| `--ttl GIÂY` | Thời hạn mỗi lượt thuê (mặc định 600) |
| `--check-url URL` | Trang kiểm tra IP thay cho danh sách của VPS |
| `--job-ref MÃ` | Mã việc ghi kèm lượt thuê để tra cứu trên VPS |
| `--rotate` | Xin VPS đổi IP proxy 4G ngay sau khi trả (proxy có link đổi IP hoặc `{session}`) |

Chỉ dùng cho `open`:

| Tham số | Ý nghĩa |
| --- | --- |
| `URL ...` | Các trang cần mở; thiếu `http://` hoặc `https://` thì tự thêm `https://` |
| `--headless` | Chạy Chromium không hiện cửa sổ |
| `--keep-open` | Giữ Chromium mở tới khi bạn đóng cửa sổ hoặc nhấn Ctrl+C; không dùng cùng `--headless` |
| `--screenshot-dir THƯ-MỤC` | Lưu ảnh chụp từng trang (`01-example.com.png`, `02-...`) |
| `--timeout GIÂY` | Thời gian chờ mỗi trang tải xong (mặc định 45) |
| `--skip-check` | Không kiểm tra IP ra trước khi mở Chromium |

### Mã thoát

| Mã | Ý nghĩa |
| --- | --- |
| `0` | Thành công (với `check`/`open`: proxy dùng tốt) |
| `1` | Lỗi kết nối tới VPS, hoặc không mở được Chromium |
| `2` | Sai cấu hình hoặc token |
| `3` | Proxy không dùng được: bị trang đích chặn, proxy lỗi, hoặc chưa mở được trang nào |
| `4` | Hết thời gian chờ mà VPS vẫn chưa có proxy rảnh |
| `130` | Người dùng dừng bằng Ctrl+C |

### Kết quả dạng JSON

Với `--json`, agent in đúng một object JSON ra stdout khi kết thúc, mọi thông báo khác ra stderr, tiện cho script gọi
agent. Ví dụ `check --json`:

```json
{
  "command": "check",
  "ok": true,
  "outcome": "ok",
  "detail": "Mở được 1 trang qua proxy",
  "proxy": {"id": 4, "label": "#4 http://127.0.0.1:36047 (pool vn-static)", "kind": "static", "pool": "vn-static", "protocol": "http"},
  "exit_ip": "198.51.100.4",
  "country": "VN",
  "visits": [
    {"url": "http://ip-check.sim/json", "status": 200, "final_url": null, "title": null, "error": null, "elapsed_ms": 2, "screenshot": null}
  ],
  "release": {"released": true, "rotation_scheduled": false, "quarantined_until": null},
  "release_error": null,
  "bridge": {"requests": 1, "upstream_failures": 0, "target_failures": 0, "bytes_sent": 325, "bytes_received": 160}
}
```

- `outcome`: kết quả đã báo cho VPS (`ok`, `blocked`, `failed`, `cancelled`), `detail` là lý do.
- `visits`: từng trang đã mở, kể cả trang kiểm tra IP.
- `release`: VPS đã nhận lại proxy chưa, có xếp lịch đổi IP không, proxy bị tạm cách ly tới khi nào. `release_error`
  khác `null` nghĩa là chưa trả được (VPS sẽ tự thu hồi khi lượt thuê hết hạn).
- `bridge`: số request và lưu lượng (byte) đi qua proxy, số lỗi do proxy và do trang đích.

Khi lỗi, object có dạng `{"command": "check", "ok": false, "exit_code": 4, "error": "Không thuê được proxy: ..."}`.
`ping --json` trả `server_url`, `agent`, `worker_id`, `server_version`, `agent_version`, `latency_ms`,
`clock_skew_sec` (đồng hồ VPS nhanh hơn máy PC bao nhiêu giây) và `check_urls`.

## Agent hoạt động thế nào

1. Gọi `ping` để xác nhận token và lấy danh sách trang kiểm tra IP (giống hệt danh sách server dùng).
2. Thuê proxy theo pool và loại đã chọn. VPS chưa có proxy rảnh thì chờ theo gợi ý của VPS rồi thử lại, tới hết
   `lease_wait_sec`.
3. Gia hạn lượt thuê ở nền, trước khi hết 1/3 thời hạn (không tin hẳn đồng hồ máy PC). VPS báo lượt thuê đã kết thúc thì
   dừng dùng proxy ngay.
4. Mở một cầu nối proxy trên `127.0.0.1` với mật khẩu ngẫu nhiên mỗi lần chạy, chuyển tiếp tới proxy thật (HTTP, HTTPS
   hoặc SOCKS5, có hoặc không có mật khẩu). Chromium không tự đăng nhập được proxy SOCKS5 có mật khẩu nên luôn đi qua
   cầu nối. Tên miền đích do proxy phân giải; cầu nối đếm lưu lượng và phân biệt lỗi do proxy với lỗi do trang đích.
5. Kiểm tra IP ra qua cầu nối, thử lần lượt các trang kiểm tra; với `open`, mở Chromium qua cầu nối (WebRTC không được
   gửi UDP ra ngoài proxy, tránh lộ IP thật của máy).
6. Chấm kết quả, trong đó lỗi của chính proxy được xét trước:
   - `failed`: không kết nối được proxy, sai mật khẩu, lỗi TLS, proxy trả HTTP 502/504.
   - `blocked`: trang đích trả HTTP 403 hoặc 429.
   - `ok`: mở được trang.
   - `cancelled`: chưa mở được trang nào, hoặc người dùng dừng giữa chừng; VPS không tính điểm cho proxy.
7. Luôn trả proxy kèm kết quả, kể cả khi lỗi hay nhấn Ctrl+C. VPS dùng kết quả để chấm điểm, tạm cách ly proxy lỗi
   nhiều lần hoặc đổi IP proxy 4G bị chặn.

## Khắc phục sự cố

| Thông báo | Cách xử lý |
| --- | --- |
| `VPS từ chối token agent` | Token không nằm trong `AGENT_TOKENS` trên VPS (kiểm tra thừa/thiếu ký tự). Sau khi sửa `.env` trên VPS, chạy `sudo docker compose up -d --force-recreate app` |
| `VPS chưa bật API cho agent` | Điền `AGENT_TOKENS` trong `.env` trên VPS rồi tạo lại container như trên |
| `Không kết nối được tới VPS` | Kiểm tra địa chỉ, mạng, tường lửa; thử mở `https://tên-miền/api/health` bằng trình duyệt trên máy PC |
| `Không tìm thấy API agent` | `server_url` chỉ cần `https://tên-miền`, không thêm `/api` |
| `Không thuê được proxy: ...` | Pool không có proxy đang sống, hoặc mọi proxy đang bận, đang đổi IP hay bị cách ly. Xem trang "Kho proxy" trên dashboard, kiểm tra tên pool, tăng `--wait` hoặc số máy dùng chung (`concurrency`) của proxy |
| `Chưa tải Chromium cho Playwright` | Chạy `python -m playwright install chromium` trong môi trường ảo |
| `Máy thiếu thư viện hệ thống cho Chromium` | Chạy `python -m playwright install-deps chromium` |
| `Không mở được cửa sổ Chromium (máy không có màn hình?)` | Thêm `--headless` |
| `Cảnh báo: đồng hồ máy PC lệch ... giây so với VPS` | Bật đồng bộ giờ tự động (Windows: Cài đặt → Thời gian và ngôn ngữ → Đặt giờ tự động; Linux: `sudo timedatectl set-ntp true`) |
| `Cảnh báo: token agent đang được gửi qua http://` | Dùng `https://` cho `server_url` |
| `Lỗi chứng chỉ HTTPS khi mở ... (proxy có thể đang can thiệp kết nối HTTPS)` | Proxy giải mã HTTPS giữa đường; đừng dùng proxy đó để quét |
| Lỗi chứng chỉ khi gọi VPS trong mạng công ty có kiểm tra HTTPS | Agent dùng biến `SSL_CERT_FILE` (chứng chỉ gốc của công ty) và `HTTPS_PROXY` cho kết nối tới VPS; kết nối qua proxy đã thuê không dùng các biến này |
| `VPS đã kết thúc lượt thuê: ...` | Lượt thuê hết hạn (máy mất mạng lâu hơn thời hạn thuê) hoặc token bị thu hồi; chạy lại lệnh |

## Chạy test

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
pytest
ruff check . && ruff format --check .
mypy commentscope_agent tests
```

Test dựng proxy HTTP/HTTPS/SOCKS5 giả và một VPS giả ngay trên máy. Test proxy HTTPS cần lệnh `openssl` để tạo chứng chỉ;
test mở Chromium thật tự bỏ qua nếu chưa tải Chromium.
