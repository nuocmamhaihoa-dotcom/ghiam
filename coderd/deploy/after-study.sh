#!/bin/sh
set -eu
cd /opt/coderd
PY=/opt/coderd/.venv/bin/python
HOURS="${CODERD_STUDY_HOURS:-10}"
if "$PY" -m worker.study --check-hours "$HOURS"; then
  systemctl start coderd-learn.timer || true
fi
