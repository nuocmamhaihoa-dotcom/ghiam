#!/usr/bin/env bash
# Copy SQLite và bare git, giữ 7 bản mới nhất.
set -euo pipefail

DB_PATH="${1:-${CODERD_DB:-/var/lib/coderd/coderd.db}}"
GIT_PATH="${2:-${GIT_BARE:-/var/lib/coderd/git/workspace.git}}"
DEST="${3:-${CODERD_BACKUP_DIR:-/var/backups/coderd}}"
STAMP="$(date -u +%Y%m%dT%H%M%S%N)"
TARGET="$DEST/$STAMP"

mkdir -p "$TARGET"
copied=0
if [[ -f "$DB_PATH" ]]; then
  cp -a "$DB_PATH" "$TARGET/coderd.db"
  copied=1
fi
if [[ -e "$GIT_PATH" ]]; then
  mkdir -p "$TARGET/git"
  cp -a "$GIT_PATH"/. "$TARGET/git/"
  copied=1
fi
if [[ "$copied" -eq 0 ]]; then
  rm -rf "$TARGET"
  echo "không có gì để backup" >&2
  exit 1
fi

mapfile -t old < <(ls -1dt "$DEST"/*/ 2>/dev/null || true)
if ((${#old[@]} > 7)); then
  for dir in "${old[@]:7}"; do
    rm -rf "$dir"
  done
fi
echo "$TARGET"
