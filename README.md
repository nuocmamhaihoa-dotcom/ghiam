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

## Cài đặt (1 máy)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
playwright install-deps chromium   # nếu thiếu lib hệ thống

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
