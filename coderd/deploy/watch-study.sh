#!/bin/sh
set -eu
cd /opt/coderd
if systemctl is-active --quiet coderd-learn-6h.service; then
  exit 0
fi
PY=/opt/coderd/.venv/bin/python
HOURS="${CODERD_STUDY_HOURS:-10}"
if "$PY" -m worker.study --check-hours "$HOURS"; then
  systemctl start coderd-learn.timer || true
  exit 0
fi
systemctl start coderd-learn-6h.service
