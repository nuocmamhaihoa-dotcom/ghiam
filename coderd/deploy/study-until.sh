#!/bin/sh
set -eu
cd /opt/coderd
export HOME=/opt/coderd/var/home
PY=/opt/coderd/.venv/bin/python
HOURS="${CODERD_STUDY_HOURS:-10}"
while true; do
  "$PY" -m worker.study --hours "$HOURS" || true
  if "$PY" -m worker.study --check-hours "$HOURS"; then
    exit 0
  fi
  sleep 2
done
