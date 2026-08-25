#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.run/hall_agent.pid"
LEGACY_PID_FILE="$ROOT_DIR/.run/$(printf '%s_%s' table agent).pid"

find_active_pid_file() {
    local candidate pid
    for candidate in "$PID_FILE" "$LEGACY_PID_FILE"; do
        if [[ -f "$candidate" ]]; then
            pid="$(tr -d '[:space:]' < "$candidate")"
            if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
                printf '%s\n%s\n' "$candidate" "$pid"
                return 0
            fi
            rm -f "$candidate"
        fi
    done
    return 1
}

ACTIVE_INFO="$(find_active_pid_file || true)"
if [[ -z "$ACTIVE_INFO" ]]; then
    echo "hall_agent is not running (PID file not found)"
    exit 0
fi

PID_FILE="$(printf '%s\n' "$ACTIVE_INFO" | sed -n '1p')"
PID="$(printf '%s\n' "$ACTIVE_INFO" | sed -n '2p')"

kill "$PID"
for _ in $(seq 1 50); do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE" "$LEGACY_PID_FILE"
        echo "hall_agent stopped"
        exit 0
    fi
    sleep 0.2
done

echo "hall_agent did not stop within 10 seconds (PID $PID)" >&2
exit 1
