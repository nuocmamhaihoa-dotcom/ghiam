#!/usr/bin/env bash
# Cài worker coderd cho user hiện tại. Không dùng quyền root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${CODERD_PREFIX:-$HOME/.local/share/coderd}"
mkdir -p "$PREFIX" "$HOME/.config/coderd" "$HOME/.config/systemd/user"
rm -rf "$PREFIX/worker"
cp -a "$ROOT/worker" "$PREFIX/worker"
cp -a "$ROOT/worker/requirements.txt" "$PREFIX/requirements.txt"

if [[ "${CODERD_SKIP_PIP:-0}" != "1" ]]; then
  python3 -m venv "$PREFIX/.venv"
  "$PREFIX/.venv/bin/pip" install -r "$ROOT/worker/requirements.txt"
fi

ENV_FILE="$HOME/.config/coderd/worker.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
CODERD_URL=http://127.0.0.1:8080
WORKER_TOKEN=
WORKSPACE_ROOT=$HOME/coderd-workspace
EOF
  chmod 600 "$ENV_FILE"
fi

sed "s|%CODERD_PREFIX%|$PREFIX|g" "$ROOT/worker/coderd-worker.service" > "$HOME/.config/systemd/user/coderd-worker.service"

if [[ "${CODERD_INSTALL_ENABLE:-1}" == "1" ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
  systemctl --user enable --now coderd-worker.service || true
fi
echo "$PREFIX"
