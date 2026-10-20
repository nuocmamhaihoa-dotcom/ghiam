#!/usr/bin/env bash
# Cài model mở Qwen2.5 Coder 0.5B bằng llamafile, chỉ nghe trên máy local.
set -euo pipefail

PREFIX="${CODERD_PREFIX:-/opt/coderd}"
MODEL_URL="${CODERD_MODEL_URL:-https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf}"
LLAMAFILE_URL="${CODERD_LLAMAFILE_URL:-https://github.com/mozilla-ai/llamafile/releases/download/0.10.6/llamafile-0.10.6-thin}"

mkdir -p "$PREFIX/bin" "$PREFIX/models"
if [[ ! -x "$PREFIX/bin/llamafile" ]]; then
  curl -fL --retry 3 -o "$PREFIX/bin/llamafile" "$LLAMAFILE_URL"
  chmod 755 "$PREFIX/bin/llamafile"
fi
if [[ ! -s "$PREFIX/models/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf" ]]; then
  curl -fL --retry 3 -o "$PREFIX/models/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf" "$MODEL_URL"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p "$PREFIX/var/home"
  chown -R coder:coder "$PREFIX/bin" "$PREFIX/models" "$PREFIX/var/home"
  install -m 644 "$(cd "$(dirname "$0")" && pwd)/coderd-model.service" /etc/systemd/system/coderd-model.service
  systemctl daemon-reload
  systemctl enable --now coderd-model.service
fi

echo "model coderd nghe tại 127.0.0.1:11434"
