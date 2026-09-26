#!/usr/bin/env bash
# Wizard cấu hình nhanh tài nguyên PC cho PA1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cores="$(nproc 2>/dev/null || echo 4)"
ram_gb="$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 8)"
default_workers=$(( cores * 7 / 10 ))
[[ "$default_workers" -lt 1 ]] && default_workers=1
[[ "$default_workers" -gt 14 ]] && default_workers=14

echo "=== Cấu hình fb-poller theo tài nguyên PC ==="
echo "Phát hiện: ${cores} CPU, ~${ram_gb} GB RAM"
echo

read -r -p "WORKER_ID [$(hostname)-1]: " worker_id
worker_id="${worker_id:-$(hostname)-1}"

read -r -p "WORKERS (số browser song song) [$default_workers]: " workers
workers="${workers:-$default_workers}"

read -r -p "HOT_INTERVAL_SEC [45]: " hot_interval
hot_interval="${hot_interval:-45}"

read -r -p "HOT_SIZE [100]: " hot_size
hot_size="${hot_size:-100}"

mkdir -p data logs
touch data/posts.txt data/proxies_static.txt data/proxies_4g.txt

cat > .env <<EOF
DATABASE_URL=sqlite+aiosqlite://${ROOT}/data/fb_poller.db
WORKER_ID=${worker_id}
ROLE=all
WORKERS=${workers}
WARM_COLD_MAX_INFLIGHT=3
HOT_SIZE=${hot_size}
HOT_INTERVAL_SEC=${hot_interval}
WARM_INTERVAL_SEC=150
COLD_INTERVAL_SEC=600
BOOST_ON_NEW_SEC=1200
HOT_JOB_TIMEOUT_MS=7000
HOT_MAX_COMMENTS_PAGE=20
BROWSER_RESTART_EVERY_JOBS=80
HEADLESS=true
NAVIGATION_TIMEOUT_MS=15000
PROXIES_STATIC_FILE=${ROOT}/data/proxies_static.txt
PROXIES_4G_FILE=${ROOT}/data/proxies_4g.txt
DATA_DIR=${ROOT}/data
EOF

echo
echo "Đã ghi .env"
if [[ -x .venv/bin/python ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python scripts/pc_doctor.py
fi
echo
echo "Tiếp theo: dán URL/proxy vào data/, rồi:"
echo "  ./scripts/fb-poller-ctl.sh import"
echo "  ./scripts/fb-poller-ctl.sh start"
