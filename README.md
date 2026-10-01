# CommentScope

Phần mềm thu thập comment công khai của các bài viết công khai. Người vận hành đưa vào danh sách permalink bài viết, các
máy PC (worker) chạy Chromium qua proxy để mở bài và lấy comment, kết quả trả về dạng JSON gồm `text`, `author`, `time`,
`likes`. Một VPS làm control plane: dashboard, API, cơ sở dữ liệu, kho proxy và điều phối các máy PC.

Kế hoạch chi tiết cho toàn hệ thống (kiến trúc, khuôn dữ liệu kết quả, các giai đoạn tiếp theo, rủi ro, các quyết định
cần chốt): [docs/KE_HOACH_TRIEN_KHAI.md](docs/KE_HOACH_TRIEN_KHAI.md).

## Hiện trạng

Đã chạy được:

- **Kho proxy trên VPS**: thêm proxy tĩnh và proxy 4G xoay hàng loạt (dán hoặc kéo-thả file `.txt`, xem trước từng dòng
  trước khi nhập), tự kiểm tra sống/chết, đổi IP proxy 4G (link đổi IP, `{session}`, nhà cung cấp tự xoay), cho máy PC
  thuê proxy theo lượt có thời hạn, chấm điểm và tạm cách ly proxy hỏng.
- **Dashboard** trang "Kho proxy": thống kê, bộ lọc, thao tác hàng loạt (kiểm tra, đổi IP, bật/tắt, chuyển pool, xoá),
  xuất file.
- **Job quét comment Facebook**: dán permalink bài viết công khai, xem trước, tạo job. Máy PC nhận việc, mở bài bằng
  Chromium qua proxy và đọc comment đang hiện.
- **Kết quả comment**: xem nội dung, tác giả, thời điểm, lượt thích; tải CSV, JSON hoặc NDJSON.
- **Agent trên máy PC** ([agent/README.md](agent/README.md)): thuê proxy từ VPS, kiểm tra IP ra, mở Chromium qua proxy
  (proxy HTTP, HTTPS, SOCKS5, có hoặc không có mật khẩu), `scrape` một permalink hoặc `run` để nhận việc liên tục.
- **Đóng gói VPS**: Docker Compose gồm PostgreSQL, server và Caddy (HTTPS tự động).

Chưa có: trang quản lý máy PC trên dashboard (menu vẫn ghi "Sắp có"). CommentScope không đăng nhập Facebook và không
giải captcha. Bài bị tường đăng nhập được đánh dấu bị chặn rồi thử proxy khác. Chi tiết ở
[mục 4 của kế hoạch](docs/KE_HOACH_TRIEN_KHAI.md#4-hiện-trạng).

## Kiến trúc

- **VPS (control plane)**: server FastAPI phục vụ API và dashboard, PostgreSQL lưu dữ liệu, Caddy lo HTTPS. Bộ lập lịch
  nền chạy trong server: kiểm tra proxy định kỳ, đổi IP proxy 4G, thu hồi lượt thuê hết hạn. VPS không chạy trình duyệt
  nên cấu hình nhỏ là đủ.
- **Máy PC (worker)**: agent Python tự gọi lên VPS qua HTTPS (máy ở sau NAT hay mạng 4G vẫn chạy, không cần mở cổng),
  thuê proxy, mở cầu nối proxy cục bộ và điều khiển Chromium bằng Playwright.
- **Proxy** được quản lý tập trung trên VPS. Máy PC thuê proxy theo lượt, dùng xong trả lại kèm kết quả (dùng tốt / bị
  chặn / lỗi) để VPS chấm điểm, cách ly proxy hỏng hoặc đổi IP proxy 4G.

Sơ đồ và các quyết định kiến trúc: [mục 3 của kế hoạch](docs/KE_HOACH_TRIEN_KHAI.md#3-kiến-trúc).

## Cấu trúc thư mục

```
server/              Server FastAPI (API, bộ lập lịch, kho proxy), migration Alembic, test
web/                 Dashboard React + Vite + Tailwind; bản build do server phục vụ
agent/               Agent chạy trên máy PC (Python + Playwright)
deploy/Caddyfile     Cấu hình Caddy (HTTPS, header bảo mật)
docs/                Kế hoạch triển khai
Dockerfile           Image gồm server và dashboard đã build
docker-compose.yml   PostgreSQL + server + Caddy cho VPS
.env.example         Mẫu cấu hình cho VPS
```

## Chạy thử trên máy phát triển

Cần Python 3.12 trở lên (agent chỉ cần 3.11), Node.js 22.13 trở lên và `openssl`. Các lệnh dưới đây viết cho Linux và
macOS; trên Windows, kích hoạt môi trường ảo bằng `.venv\Scripts\activate` và tạo file `.env` bằng trình soạn thảo.

### 1. Server

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cat > .env <<EOF
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
ADMIN_PASSWORD=doi-mat-khau-nay
AGENT_TOKENS=$(openssl rand -hex 24)
EOF

uvicorn app.main:create_app --factory --port 8000
```

- Sửa `ADMIN_PASSWORD` trong `server/.env` thành mật khẩu của bạn (ít nhất 8 ký tự). Các khoá chỉ tạo một lần rồi giữ
  nguyên: đổi `ENCRYPTION_KEY` là không giải mã được mật khẩu proxy đã lưu.
- Server đọc `.env` ở thư mục đang đứng, nên luôn chạy lệnh `uvicorn` trong `server/`. Mặc định dữ liệu nằm trong file
  SQLite `server/commentscope.db`; migration tự chạy khi khởi động.
- Tài liệu API tương tác: <http://127.0.0.1:8000/api/docs>.

### 2. Dashboard

```bash
cd web
npm ci
npm run dev
```

Mở <http://localhost:5173> và đăng nhập bằng tài khoản `admin` với `ADMIN_PASSWORD`. Vite chuyển các request `/api` sang
server ở `http://127.0.0.1:8000` (đổi bằng biến `VITE_API_TARGET`).

Muốn server tự phục vụ dashboard như trên VPS: chạy `npm run build`, thêm `WEB_DIST_DIR=../web/dist` vào `server/.env`,
khởi động lại server rồi mở <http://127.0.0.1:8000>.

### 3. Thử với mạng proxy giả lập

Không có proxy thật vẫn thử được toàn bộ luồng nhờ mạng giả lập gồm proxy HTTP, proxy SOCKS5, proxy "4G" có link đổi
IP, cổng proxy xoay theo `{session}` và vài proxy chết:

```bash
cd server
source .venv/bin/activate
python -m tests.netsim
```

Lệnh in ra một dòng `PROXY_CHECK_URLS=http://ip-check.sim/json` và danh sách proxy. Thêm dòng đó vào `server/.env`, khởi
động lại server, rồi bấm "Thêm proxy hàng loạt" trên dashboard và dán danh sách proxy. Proxy giả lập chỉ trả lời như một
trang kiểm tra IP, không ra Internet thật: khi thử agent, mở `http://ip-check.sim/json` thay vì trang web thật.

### 4. Agent

Cài đặt và cách dùng đầy đủ (kể cả trên Windows): [agent/README.md](agent/README.md). Thử nhanh với server ở trên:

```bash
cd agent
python3 -m venv .venv
source .venv/bin/activate
pip install .
python -m playwright install chromium

export COMMENTSCOPE_SERVER_URL=http://127.0.0.1:8000
export COMMENTSCOPE_AGENT_TOKEN="$(grep '^AGENT_TOKENS=' ../server/.env | cut -d= -f2 | cut -d, -f1)"
commentscope-agent ping
commentscope-agent check --pool vn-static
commentscope-agent open http://ip-check.sim/json --pool 4g-gateway
```

Đọc comment Facebook: trên dashboard mở "Job quét comment", dán permalink bài công khai rồi tạo job. Trên máy PC chạy
`commentscope-agent run` để nhận việc, hoặc `commentscope-agent scrape` với một permalink. Mạng giả lập ở mục 3 không
phải Facebook. Agent không đăng nhập; nếu Facebook yêu cầu đăng nhập thì bài được đánh dấu bị chặn và thử proxy khác.

## Triển khai lên VPS

### Chuẩn bị

- VPS Linux (ví dụ Ubuntu 24.04). VPS không chạy trình duyệt nên cấu hình nhỏ là đủ; nên có từ 2 GB RAM để build image
  (bước build dashboard dùng Node.js).
- Một tên miền có bản ghi A (và AAAA nếu dùng IPv6) trỏ về IP của VPS.
- Cổng 80/TCP, 443/TCP và 443/UDP (HTTP/3) mở từ Internet. Caddy dùng cổng 80 để xin chứng chỉ Let's Encrypt.

### Cài đặt

1. Cài Docker Engine và Docker Compose plugin, ví dụ bằng script chính thức:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   ```

2. Lấy mã nguồn:

   ```bash
   git clone https://github.com/nuocmamhaihoa-dotcom/ghiam.git commentscope
   cd commentscope
   ```

3. Tạo file cấu hình và điền giá trị (mỗi biến có chú thích và lệnh tạo giá trị ngẫu nhiên ngay trong file):

   ```bash
   cp .env.example .env
   chmod 600 .env
   nano .env
   ```

   Bắt buộc: `DOMAIN`, `ACME_EMAIL`, `POSTGRES_PASSWORD`, `SECRET_KEY`, `ENCRYPTION_KEY`, `ADMIN_PASSWORD`,
   `AGENT_TOKENS` (mỗi máy PC một token, cách nhau bằng dấu phẩy).

4. Build và chạy:

   ```bash
   sudo docker compose up -d --build
   ```

5. Kiểm tra:

   ```bash
   sudo docker compose ps                # app phải ở trạng thái healthy
   sudo docker compose logs -f app       # log server, Ctrl+C để thoát
   curl https://tên-miền-của-bạn/api/health
   ```

   Mở `https://tên-miền-của-bạn`, đăng nhập bằng `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Nếu trình duyệt báo lỗi chứng
   chỉ, xem `sudo docker compose logs caddy` (thường do tên miền chưa trỏ đúng IP hoặc cổng 80 bị chặn).

6. Trên mỗi máy PC: cài agent theo [agent/README.md](agent/README.md), dùng địa chỉ `https://tên-miền-của-bạn` và một
   token trong `AGENT_TOKENS`.

Chỉ Caddy mở cổng ra ngoài. Server (cổng 8000) và PostgreSQL chỉ nằm trong mạng nội bộ của Docker.

Không tăng số bản của server (không `--scale app=2`, không thêm `--workers` cho uvicorn): bộ lập lịch kiểm tra proxy và
đổi IP chạy ngay trong tiến trình server, chạy hai bản sẽ đổi IP hai lần.

### Tường lửa

Ví dụ với `ufw` trên Ubuntu:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
```

Nên đăng nhập SSH bằng khoá thay cho mật khẩu và bật tự động cập nhật bản vá bảo mật của hệ điều hành.

### Cập nhật phiên bản

Sao lưu trước (mục dưới), rồi:

```bash
cd commentscope
git pull
sudo docker compose up -d --build
```

Migration cơ sở dữ liệu tự chạy khi server khởi động.

### Sao lưu và khôi phục

Một bản sao lưu đầy đủ gồm **hai phần**, cất riêng ở nơi an toàn ngoài VPS:

- Dữ liệu: file dump của PostgreSQL.
- File `.env`: chứa `ENCRYPTION_KEY`. Mất khoá này thì mật khẩu proxy và link đổi IP trong bản sao lưu không giải mã
  được nữa. Không để `.env` chung chỗ với file dump.

Sao lưu dữ liệu:

```bash
sudo docker compose exec -T db pg_dump -U commentscope -Fc commentscope > backup-$(date +%F).dump
```

Tự động sao lưu lúc 3 giờ sáng mỗi ngày bằng cron của root (`sudo crontab -e`; trong crontab phải viết `\%` thay cho
`%`), nhớ chép bản sao lưu ra ngoài VPS:

```
0 3 * * * cd /đường-dẫn/commentscope && docker compose exec -T db pg_dump -U commentscope -Fc commentscope > /var/backups/commentscope-$(date +\%F).dump
```

Khôi phục trên VPS đang chạy (dữ liệu hiện tại bị thay bằng bản sao lưu):

```bash
sudo docker compose stop app
sudo docker compose exec -T db pg_restore -U commentscope -d commentscope --clean --if-exists < backup-2026-09-27.dump
sudo docker compose start app
```

Chuyển sang VPS mới: trỏ tên miền về IP của VPS mới, làm bước cài đặt 1–2, chép file `.env` cũ vào thư mục
`commentscope`, rồi:

```bash
sudo docker compose up -d --wait db
sudo docker compose exec -T db pg_restore -U commentscope -d commentscope --clean --if-exists < backup-2026-09-27.dump
sudo docker compose up -d --build
```

## Cấu hình

Server đọc biến môi trường hoặc file `.env`. Trên VPS, mọi biến đặt trong `.env` ở thư mục gốc; `DATABASE_URL` do
`docker-compose.yml` tự đặt.

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `DOMAIN`, `ACME_EMAIL` | (bắt buộc với Docker Compose) | Tên miền của dashboard và email nhận thông báo chứng chỉ Let's Encrypt |
| `POSTGRES_PASSWORD` | (bắt buộc với Docker Compose) | Mật khẩu PostgreSQL, chỉ dùng chữ và số |
| `SECRET_KEY` | (bắt buộc) | Khoá ký phiên đăng nhập dashboard, ít nhất 32 ký tự |
| `ENCRYPTION_KEY` | suy ra từ `SECRET_KEY` | Khoá Fernet mã hoá mật khẩu proxy và link đổi IP. Nên đặt riêng: nếu bỏ trống thì đổi `SECRET_KEY` sẽ mất dữ liệu proxy |
| `ADMIN_USERNAME` | `admin` | Tên đăng nhập dashboard |
| `ADMIN_PASSWORD` | (bắt buộc) | Mật khẩu dashboard, ít nhất 8 ký tự |
| `AGENT_TOKENS` | trống | Token của agent, cách nhau bằng dấu phẩy. Trống thì API agent trả lỗi 503 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./commentscope.db` | Chuỗi kết nối cơ sở dữ liệu; PostgreSQL dùng dạng `postgresql+asyncpg://...` |
| `AUTO_MIGRATE` | `true` | Tự chạy migration khi khởi động |
| `LOG_LEVEL` | `INFO` | Mức log |
| `ACCESS_TOKEN_TTL_MINUTES` | `720` | Thời hạn phiên đăng nhập dashboard (phút) |
| `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SEC` | `10`, `300` | Khoá đăng nhập tạm thời theo IP khi sai quá số lần trong khoảng thời gian này |
| `WEB_DIST_DIR` | trống | Thư mục bản build dashboard để server phục vụ (image Docker đã đặt sẵn) |
| `CORS_ORIGINS` | trống | Chỉ cần khi dashboard chạy ở tên miền khác server, cách nhau bằng dấu phẩy |
| `PROXY_CHECK_URLS` | `https://ipinfo.io/json,https://api.ipify.org?format=json` | Trang kiểm tra IP ra của proxy, thử lần lượt |
| `PROXY_CHECK_TIMEOUT_SEC` | `15` | Thời gian chờ mỗi lần kiểm tra proxy |
| `PROXY_CHECK_CONCURRENCY` | `50` | Số proxy kiểm tra song song |
| `PROXY_HEALTH_CHECK_INTERVAL_SEC` | `900` | Chu kỳ tự kiểm tra lại toàn bộ kho proxy (0 = tắt) |
| `PROXY_HEALTH_CHECK_BATCH` | `200` | Số proxy đưa vào hàng đợi kiểm tra mỗi nhịp lập lịch |
| `PROXY_ROTATION_CONCURRENCY` | `20` | Số lượt đổi IP chạy song song |
| `PROXY_ROTATION_CALL_TIMEOUT_SEC` | `30` | Thời gian chờ khi gọi link đổi IP |
| `PROXY_ROTATION_SETTLE_SEC` | `8` | Chờ sau khi gọi link đổi IP rồi mới kiểm tra IP mới |
| `PROXY_ROTATION_VERIFY_ATTEMPTS`, `PROXY_ROTATION_VERIFY_INTERVAL_SEC` | `4`, `5` | Số lần và khoảng cách kiểm tra IP mới sau khi đổi |
| `PROXY_FAILURE_THRESHOLD` | `3` | Số lần lỗi liên tiếp thì bắt đầu cách ly proxy |
| `PROXY_QUARANTINE_BASE_SEC`, `PROXY_QUARANTINE_MAX_SEC` | `300`, `3600` | Thời gian cách ly lần đầu (gấp đôi ở mỗi lần lỗi tiếp theo) và tối đa |
| `PROXY_LEASE_DEFAULT_TTL_SEC`, `PROXY_LEASE_MAX_TTL_SEC` | `600`, `3600` | Thời hạn mặc định và tối đa của một lượt máy PC thuê proxy |
| `PROXY_LEASE_RETENTION_DAYS` | `7` | Số ngày giữ lịch sử thuê proxy |
| `PROXY_IMPORT_MAX_LINES` | `20000` | Số dòng tối đa mỗi lần nhập proxy |
| `SCHEDULER_ENABLED`, `SCHEDULER_TICK_SEC` | `true`, `5` | Bật bộ lập lịch nền và chu kỳ mỗi nhịp (giây) |

## Nhập proxy hàng loạt

Bấm "Thêm proxy hàng loạt" trên trang "Kho proxy", dán danh sách (hoặc kéo-thả file `.txt`), xem trước rồi nhập. Mỗi dòng
một proxy; link đổi IP và tuỳ chọn riêng từng dòng viết sau dấu `|`:

```
# Proxy tĩnh
203.0.113.10:8080
203.0.113.11:8080:user:pass
user:pass@203.0.113.12:8080
socks5://user:pass@203.0.113.13:1080|pool=vn-static

# Proxy 4G có link đổi IP của nhà cung cấp
4g.example.com:10001:user:pass|https://4g.example.com/api/change-ip?key=abc|pool=4g-viettel|cooldown=60s

# Proxy 4G đổi IP bằng session: {session} được thay bằng mã ngẫu nhiên mới mỗi lần đổi IP
gw.example.com:7000:user-session-{session}:pass|pool=4g-gateway|concurrency=3

# Proxy 4G do nhà cung cấp tự xoay theo chu kỳ, không có link đổi IP
gw.example.com:8000:user:pass|type=4g|interval=10m
```

Tuỳ chọn: `type` (`static`/`4g`), `pool`, `interval` (tự đổi IP theo lịch), `cooldown` (chờ tối thiểu giữa hai lần đổi),
`concurrency` (số máy PC dùng chung một proxy), `method` (`GET`/`POST` khi gọi link đổi IP), `protocol`. Dòng có link đổi
IP hoặc `{session}` tự được coi là proxy 4G. Đủ các dạng được hỗ trợ (cột cách nhau bằng tab khi copy từ Excel, IPv6...)
và cách hệ thống đổi IP, chấm điểm, cách ly proxy: [mục 5 của kế hoạch](docs/KE_HOACH_TRIEN_KHAI.md#5-kho-proxy-đã-triển-khai).

## API

Tài liệu API tương tác (Swagger) ở `/api/docs`, đặc tả OpenAPI ở `/api/openapi.json`.

| Nhóm | Xác thực | Endpoint |
| --- | --- | --- |
| Đăng nhập | Không | `POST /api/auth/login` (JSON `username`, `password`) trả `access_token`; `GET /api/auth/me` |
| Kho proxy | `Authorization: Bearer <access_token>` | `GET /api/proxies` (lọc, phân trang), `GET /api/proxies/stats`, `POST /api/proxies/import/preview`, `POST /api/proxies/import`, `GET /api/proxies/export`, `POST /api/proxies/bulk`, `GET/PATCH/DELETE /api/proxies/{id}`, `POST /api/proxies/{id}/check`, `POST /api/proxies/{id}/rotate` |
| Agent | `Authorization: Bearer <token trong AGENT_TOKENS>` | `GET /api/agent/ping`, `POST /api/agent/proxies/lease`, `POST /api/agent/leases/{lease_id}/renew`, `POST /api/agent/leases/{lease_id}/release` |
| Sức khoẻ | Không | `GET /api/health` |

Tự viết công cụ riêng thay cho agent cũng được: thuê proxy, gia hạn trước khi hết hạn, rồi trả kèm kết quả `ok`,
`blocked`, `failed` hoặc `cancelled`.

```bash
curl -s -X POST https://tên-miền-của-bạn/api/agent/proxies/lease \
  -H "Authorization: Bearer $COMMENTSCOPE_AGENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"worker_id": "pc-01", "pool": "vn-static", "ttl_sec": 600}'
```

Phản hồi có `lease_id`, `expires_at` và `proxy` (kèm trường `playwright` dùng thẳng cho Playwright với proxy HTTP/HTTPS;
Chromium không tự đăng nhập được proxy SOCKS5 có mật khẩu, agent xử lý việc này bằng cầu nối cục bộ). Chưa có proxy rảnh
thì `lease_id` là `null` và `retry_after_sec` cho biết nên thử lại sau bao lâu.

## Kiểm thử

Server:

```bash
cd server
source .venv/bin/activate
pytest
ruff check . && ruff format --check .
mypy app tests
```

Test chạy trên SQLite; đặt thêm `TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@localhost:5432/ten_db` để chạy cả trên
PostgreSQL. **Dùng một database riêng cho test**: test xoá và tạo lại các bảng trong database đó.

Dashboard:

```bash
cd web
npm ci
npm run lint && npm run typecheck && npm test && npm run build
```

Agent:

```bash
cd agent
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
pytest
ruff check . && ruff format --check .
mypy commentscope_agent tests
```

Test của agent dựng proxy HTTP/HTTPS/SOCKS5 giả và một VPS giả ngay trên máy; phần mở Chromium thật tự bỏ qua nếu chưa
tải Chromium.

## Bảo mật

- Không commit file `.env`, `agent/config.json` hay file cơ sở dữ liệu (đã có trong `.gitignore`). Token và mật khẩu
  chỉ đặt qua biến môi trường hoặc file cấu hình.
- Mật khẩu proxy và link đổi IP được mã hoá trong cơ sở dữ liệu; danh sách proxy trên dashboard chỉ hiện mật khẩu dạng
  che. File xuất có mật khẩu, hãy giữ kín.
- Nên cấp mỗi máy PC một token trong `AGENT_TOKENS` để thu hồi riêng khi mất máy.
- Chi tiết và các việc bảo mật còn phải làm: [mục 10 của kế hoạch](docs/KE_HOACH_TRIEN_KHAI.md#10-bảo-mật).

## Lưu ý pháp lý

Nhiều nền tảng mạng xã hội cấm thu thập dữ liệu tự động khi chưa được cho phép, và tên, mã định danh, nội dung comment
của người dùng là dữ liệu cá nhân theo pháp luật Việt Nam. Chỉ thu thập nội dung công khai, ưu tiên API chính thức khi
có, giới hạn tốc độ, tối thiểu hoá và đặt thời hạn lưu dữ liệu; nên hỏi ý kiến luật sư trước khi chạy ở quy mô lớn. Xem
[mục 11 của kế hoạch](docs/KE_HOACH_TRIEN_KHAI.md#11-pháp-lý-điều-khoản-nền-tảng-và-dữ-liệu-cá-nhân).
