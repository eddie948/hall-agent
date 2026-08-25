#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
PID_FILE="$RUN_DIR/hall_agent.pid"
LEGACY_PID_FILE="$RUN_DIR/$(printf '%s_%s' table agent).pid"
PYTHON_BIN="${PYTHON_BIN:-python}"

cd "$ROOT_DIR"
LOG_DIR="${LOG_DIR:-$("$PYTHON_BIN" -c 'from src.config import settings; print(settings.log_dir)')}"
APP_PORT_VALUE="${APP_PORT:-$("$PYTHON_BIN" -c 'from src.config import settings; print(settings.app_port)')}"
LOG_DAY="$("$PYTHON_BIN" -c 'from datetime import datetime; from zoneinfo import ZoneInfo; from src.config import settings; print(datetime.now(ZoneInfo(settings.scheduler_timezone)).strftime("%Y-%m-%d"))')"

mkdir -p "$RUN_DIR" "$LOG_DIR"

for CANDIDATE_PID_FILE in "$PID_FILE" "$LEGACY_PID_FILE"; do
    if [[ -f "$CANDIDATE_PID_FILE" ]]; then
        PID="$(tr -d '[:space:]' < "$CANDIDATE_PID_FILE")"
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            if [[ "$CANDIDATE_PID_FILE" != "$PID_FILE" ]]; then
                printf '%s\n' "$PID" > "$PID_FILE"
            fi
            echo "hall_agent is already running (PID $PID)"
            exit 0
        fi
        rm -f "$CANDIDATE_PID_FILE"
    fi
done

LAUNCHER_LOG="$LOG_DIR/launcher_$LOG_DAY.log"

LOG_DIR="$LOG_DIR" LOG_CONSOLE="${LOG_CONSOLE:-false}" \
    nohup "$PYTHON_BIN" -m src.run </dev/null >>"$LAUNCHER_LOG" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

sleep 2
if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "hall_agent failed to start; recent launcher output:"
    tail -n 30 "$LAUNCHER_LOG" || true
    exit 1
fi

echo "hall_agent started (PID $PID)"
echo "health: http://127.0.0.1:$APP_PORT_VALUE/health"
echo "log: $LOG_DIR/app_$LOG_DAY.log"
