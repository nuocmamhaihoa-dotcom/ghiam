# fb-poller (PA1) — Tiered Facebook public comment poller

**Hướng đã chốt: PA1-only** (không Graph API trong giai đoạn này).

Thu thập **comment công khai** từ **bài viết công khai** bằng browser nhẹ (Playwright), chỉ lấy **Newest đầu trang**, **không nested replies** trong vòng Hot.

Chi tiết vận hành 2 PC: [`deploy/PA1_RUNBOOK.md`](deploy/PA1_RUNBOOK.md).

## Mục tiêu vận hành đã chốt

| Hạng mục | Giá trị |
|---|---|
| Tổng bài | 300 (scale thêm sau) |
| Hot | 100 bài |
| Chu kỳ Hot | bắt đầu **45s**, mục tiêu **30s** khi `p95≤5s` |
| Warm | ~100 @ 2–3 phút |
| Cold | còn lại @ 5–15 phút |
| Boost | có comment mới → giữ Hot 15–30 phút |
| Engine | **PA1-only** (browser nhẹ) |

> Hot 100 @ 30s là biên cứng với ~20 browser. Hệ thống mặc định `HOT_INTERVAL_SEC=45`. Hạ xuống 30 chỉ khi KPI đạt.

## Ghi nhớ phím và chuột trên máy, rồi làm lại

Phần mềm nhỏ **chạy trên máy tính của bạn**: nhập **5 ký tự cố định** (đổi được), **số bắt đầu** và **số kết thúc** của 5 số phía sau, rồi chỉ 4 nút trái nếu sau Enter màn hình có thể đổi. Từng bước: nhập **tên vị trí**, bấm **Bôi màu** (chụp màn hoặc thêm ảnh, tô hồng chỗ cần ấn) hoặc **Chọn vị trí** trên màn hình, rồi **Lưu vị trí**. Máy tìm tâm vùng bôi màu và bấm trái đúng chỗ đó, nhớ theo **tỷ lệ màn hình**. Nếu máy không nhận lần bấm, phần mềm bấm lại đúng vị trí có tên đó. **Bắt đầu** gồm cả hai kịch bản: máy gõ mã (ví dụ `SPXAB00001`), Enter, đo % đổi màn. Đủ ngưỡng (mặc định 80%): tự bấm 4 nút trái đã chỉ, lưu mã vừa gõ, rồi gõ số tiếp. F9 cũng lưu số vừa Enter. F8 dừng. VPS `http://VPS:8088/` hiện cùng 4 thẻ; Gõ/Enter/bấm thật trên máy Windows.

```bash
pip install pynput          # hoặc: pip install -e ".[macro]"
python -m pc_macro          # cửa sổ Ghi / Dừng / Làm lại
python -m pc_macro list
python -m pc_macro play "tên bản ghi" --repeat 3 --delay 2
python -m pc_macro push     # đưa kịch bản local lên VPS (cần PC_MACRO_HUB)
python -m pc_macro pull     # lấy kịch bản từ VPS về máy
```

Windows (đã có venv của agent):

```powershell
.\.venv\Scripts\pip install pynput
.\.venv\Scripts\python -m pc_macro
```

Đặt số lần lặp = `0` để làm mãi đến khi bấm Dừng. Ô **Chờ** cho bạn vài giây chuyển sang cửa sổ cần thao tác trước khi máy tự gõ / bấm.

**Kịch bản từng bước:** sau khi Dừng, bản ghi tách thành bước (gõ chữ, gõ số, phím, bấm, cuộn, chờ). Sửa / xoá / lên / xuống / thêm từng bước, hoặc **Ghi thêm** để nối thao tác mới. **Chèn vào kịch bản** ghép một bản đã lưu vào kịch bản đang sửa. Lưu rồi lần sau **Mở** lại đúng thứ tự đã chỉnh.

**Dãy số:** mỗi mã gồm **5 ký tự cố định** đứng trước + **5 số** tăng dần. Ví dụ tiền tố `SPXAB`, bắt đầu `00001`, kết thúc `00100` → máy gõ `SPXAB00001` rồi Enter, sau đó `SPXAB00002` … đến `SPXAB00100`. Sau Enter máy so sánh ảnh màn hình; đổi ≥ ngưỡng (mặc định 80%) thì lưu mã vừa gõ và bấm 4 vị trí đã ghi, rồi tiếp tục tăng số. Đổi 5 ký tự cố định bất cứ lúc nào. F9 lưu mã khi bạn thấy đổi.

**Hướng 2** (độc lập, không đụng 4 nút / 80% của hướng 1): cùng 5 ký tự + 5 số. Mỗi dãy số chạy **một trình duyệt**. **Gắn 4 ô** = bấm lần lượt ô 5 số trên 4 website: mỗi lần bấm gắn cửa sổ + ô nhập + khung theo dõi quanh ô. **Chọn 4 khung** chỉ khi muốn kéo tay. **Bắt đầu 2** gõ 4 mã rồi Enter đồng loạt. Sau Enter, chỉ lưu mã của trình duyệt có khung đổi ≥20%; các mã / trình duyệt khác vẫn chạy. **Tiếp tục** chỉ mở lại cửa sổ đã dừng. Ví dụ `00001`–`00010` → nhịp 1: `…00001 …00002 …00003 …00004`; nếu chỉ `…00002` đổi thì lần sau gõ `…00005 …00002(chờ) …00007 …00008`. F8 dừng cả hai hướng. Giao diện hiện **chỉ hiện hướng 2 + Bật VPN**; hướng 1 tạm ẩn, mã vẫn giữ.

**VPN trên PC:** một nút **Bật VPN**. Bật là tự siết DNS `1.1.1.1`/`9.9.9.9`, IPv6, WebRTC Chrome/Edge/Firefox, cài Cloudflare WARP qua `winget` nếu thiếu, rồi nối (WARP → Proton/Windscribe CLI → [VPN Gate](https://www.vpngate.net/api/iphone/) → `data/proxies_static.txt`). Tắt là ngắt và thôi khóa. IP không đổi thì không gõ số. Mã nước 2 chữ (JP/US/KR) lọc VPN Gate. Không ẩn tuyệt đối — nhà cung cấp VPN vẫn thấy máy bạn. Mật khẩu chỉ nằm trong `data/vpn.json` — không đồng bộ VPS.

**Tự cập nhật:** cài một lần (`Cai-dat.ps1`). Mỗi lần mở phần mềm (và khi đăng nhập Windows) máy hỏi `GET /v1/pc-macro/version`, nếu chữ ký khác thì tự tải `/tai/pc-macro.zip`, ghi đè `pc_macro/*.py`, rồi tự mở lại. Không cần tải zip hay chạy cài đặt lại. Thư mục `data/` (kịch bản, VPN) giữ nguyên.

## Cài đặt trên PC (dùng tài nguyên máy local)

- Linux: [`docs/CAI_DAT_PC.md`](docs/CAI_DAT_PC.md)
- **Windows agent (bật là kết nối + tự cập nhật + nhiều PC):** [`docs/AGENT_WINDOWS.md`](docs/AGENT_WINDOWS.md)
- **PC Server LAN (băng thông cao, sync comment, serve update):** [`docs/SERVER_PC.md`](docs/SERVER_PC.md)
- **VPS Hub (máy chủ gốc + dashboard comment):** [`docs/VPS_HUB.md`](docs/VPS_HUB.md)

**Windows — cài một lần, sau này tự nâng cấp:**

```powershell
powershell -ExecutionPolicy Bypass -File .\pc_agent\windows\Install-Agent.ps1 -StartNow
# dán URL/proxy vào data\ rồi:
.\.venv\Scripts\fb-poller.exe import-urls .\data\posts.txt
.\.venv\Scripts\fb-poller.exe import-proxies
.\.venv\Scripts\fb-poller.exe rebalance-hot
```

Khi bạn tạo GitHub Release `v*`, workflow đóng gói zip + `update-manifest.json`; agent trên mọi PC tự tải và cập nhật, **không cần cài lại**.

**Khuyến nghị vận hành nhiều PC:** chọn 1 máy làm LAN server (`Install-Server.ps1` / `scripts/install-server.sh`), upload zip lên đó; các PC scanner `prefer_lan` tải update + sync comment qua mạng nội bộ — tránh nghẽn Internet.

**Linux:**

```bash
chmod +x install.sh scripts/*.sh
./install.sh          # tự chọn workers theo CPU/RAM
# ./install.sh pc20   # 14 workers
# ./install.sh pc12   # 6 workers

./scripts/fb-poller-ctl.sh import
./scripts/fb-poller-ctl.sh doctor
./scripts/fb-poller-ctl.sh start
./scripts/fb-poller-ctl.sh status
```

Cài thủ công (nếu cần):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
```

## Chuẩn bị dữ liệu

1. Dán 300 permalink vào `data/posts.txt` (1 URL / dòng).
2. Dán proxy tĩnh vào `data/proxies_static.txt`.
3. (Tuỳ chọn) proxy 4G vào `data/proxies_4g.txt`.

```bash
fb-poller init-db
fb-poller import-urls data/posts.txt
fb-poller import-proxies
fb-poller rebalance-hot
fb-poller status
```

## Probe guest-visible

```bash
fb-poller probe --limit 10 --workers 1
```

Bài bị login wall / không lộ comment khi chưa login sẽ được đánh `guest_hidden` và loại khỏi poll tích cực.

## Chạy poller

Dev / Phase 1–2 (ít worker):

```bash
fb-poller run --workers 2 --hot-interval 60
```

Gần production trên PC mạnh (ví dụ PC 20 nhân):

```bash
# .env: WORKER_ID=pc20-1 WORKERS=14 HOT_INTERVAL_SEC=45
fb-poller run
```

PC control (12 nhân) có thể chạy DB + ít worker:

```bash
# WORKER_ID=pc12-1 WORKERS=6
fb-poller run
```

## CLI

| Lệnh | Việc |
|---|---|
| `fb-poller init-db` | Tạo schema |
| `fb-poller import-urls FILE` | Import URL |
| `fb-poller import-proxies` | Import proxy files |
| `fb-poller rebalance-hot` | Ép đúng `HOT_SIZE` bài Hot |
| `fb-poller set-tier ID hot\|warm\|cold` | Gán tier thủ công |
| `fb-poller probe` | Thử lấy comment guest |
| `fb-poller status` | Số liệu + poll_runs gần nhất |
| `fb-poller kpi` | Gate trước khi hạ Hot xuống 30s |
| `fb-poller run` | Scheduler + workers |

Chạy theo máy:

```bash
cp deploy/env.pc20.example deploy/env.pc20
./deploy/run_pc20.sh
# hoặc
cp deploy/env.pc12.example deploy/env.pc12
./deploy/run_pc12.sh
```

## Kiến trúc thư mục

```
fb_poller/
  config.py
  cli/main.py
  storage/          # SQLAlchemy models + repos
  workers/          # browser pool, extractor, proxy, job
  orchestrator/     # tiered scheduler + runner
data/
  posts.sample.txt
  proxies_static.txt
  proxies_4g.txt
```

## KPI go/no-go trước khi hạ Hot xuống 30s

- `hot_success_rate ≥ 85%` trong ≥2 giờ @ 45s
- `p95 latency ≤ 5s` trước khi set `HOT_INTERVAL_SEC=30`
- `blocked_rate` không tăng đột biến

## Postgres / Redis (khi scale 2 PC)

```bash
docker compose up -d
# .env:
# DATABASE_URL=postgresql+asyncpg://fb:fb@127.0.0.1:5432/fb_poller
# pip install asyncpg
```

Hiện tại queue mặc định là **in-process** (1 process / máy). Multi-PC chia bài bằng cùng Postgres + `claim_due` (stamp `next_poll_at`). Redis queue sẽ bổ sung ở phase sau nếu cần.

## Lưu ý

- Chỉ thu thập nội dung **công khai với visitor chưa đăng nhập**.
- Tuân thủ pháp luật địa phương và điều khoản nền tảng; dùng có trách nhiệm.
- Extractor DOM/GraphQL là best-effort — Facebook đổi UI có thể làm miss; theo dõi `poll_runs.error_code`.
