#!/usr/bin/env bash
# 本地调试：不连企业微信，独立端口与数据库，提供静态聊天页。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
  else
    echo "python not found; activate your env or set PYTHON_BIN" >&2
    exit 1
  fi
fi

cd "$ROOT_DIR"

export ENABLE_WECOM_BOT="${ENABLE_WECOM_BOT:-false}"
export APP_PORT="${APP_PORT:-8101}"
export DATA_DIR="${DATA_DIR:-./data/local_dev}"
export LOG_DIR="${LOG_DIR:-./data/local_dev/logs}"
export MODEL_INPUT_SNAPSHOT_DIR="${MODEL_INPUT_SNAPSHOT_DIR:-./data/local_dev/model_input_snapshots}"
export LOG_CONSOLE="${LOG_CONSOLE:-true}"
export LOGFIRE_ENABLED="${LOGFIRE_ENABLED:-false}"

mkdir -p "$DATA_DIR" "$LOG_DIR" "$MODEL_INPUT_SNAPSHOT_DIR"

echo "local UI:  http://127.0.0.1:${APP_PORT}/"
echo "health:    http://127.0.0.1:${APP_PORT}/health"
echo "data_dir:  ${DATA_DIR}"
echo "wecom bot: ${ENABLE_WECOM_BOT}"
echo "Ctrl+C to stop"
echo

exec "$PYTHON_BIN" -m src.run
