#!/usr/bin/env bash
# Cài coderd kiểu systemd, cùng cách với hub fb-poller.
# Cổng mặc định 8090 để không chiếm 8088 của hub.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${CODERD_PREFIX:-/opt/coderd}"
PORT="${APP_PORT:-8090}"
HOST="${APP_HOST:-0.0.0.0}"

mkdir -p "$PREFIX/var/git"
if [[ "$(cd "$PREFIX" && pwd)" != "$ROOT" ]]; then
  rm -rf "$PREFIX/app"
  cp -a "$ROOT/app" "$PREFIX/app"
  cp -a "$ROOT/requirements.txt" "$PREFIX/requirements.txt"
fi

if [[ ! -x "$PREFIX/.venv/bin/python" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    python3.12 -m venv "$PREFIX/.venv"
  else
    python3 -m venv "$PREFIX/.venv"
  fi
fi
"$PREFIX/.venv/bin/pip" install -q -r "$PREFIX/requirements.txt"

ENV_FILE="$PREFIX/coderd.env"
if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  auth="$("$PREFIX/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(24))')"
  worker="$("$PREFIX/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(24))')"
  cat > "$ENV_FILE" <<EOF
APP_HOST=$HOST
APP_PORT=$PORT
AUTH_TOKEN=$auth
WORKER_TOKEN=$worker
CODERD_DB=$PREFIX/var/coderd.db
GIT_BARE=$PREFIX/var/git/workspace.git
EOF
fi
chmod 600 "$ENV_FILE"
cat > "$PREFIX/run.sh" <<EOF
#!/bin/bash
set -a
source "$ENV_FILE"
set +a
cd "$PREFIX"
exec "$PREFIX/.venv/bin/python" -m app
EOF
chmod 750 "$PREFIX/run.sh"

if [[ "$(id -u)" -eq 0 ]]; then
  if ! id coder >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin coder
  fi
  run_user=coder
  chown -R coder:coder "$PREFIX"
else
  run_user="$(id -un)"
fi

unit="$(sed -e "s|%CODERD_USER%|$run_user|g" -e "s|%CODERD_PREFIX%|$PREFIX|g" "$ROOT/deploy/coderd.service")"
if [[ "$(id -u)" -eq 0 && "${CODERD_SKIP_SYSTEMD:-0}" != "1" ]] && systemctl is-system-running >/dev/null 2>&1; then
  printf '%s\n' "$unit" > /etc/systemd/system/coderd.service
  systemctl daemon-reload
  systemctl enable --now coderd.service
else
  printf '%s\n' "$unit" > "$PREFIX/coderd.service"
fi

echo "coderd đã cài tại $PREFIX cổng $PORT"
if [[ "${CODERD_START:-0}" == "1" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  cd "$PREFIX"
  exec "$PREFIX/.venv/bin/python" -m app
fi
