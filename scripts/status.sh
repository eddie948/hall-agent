#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT_DIR/.run/hall_agent.pid"
LEGACY_PID_FILE="$ROOT_DIR/.run/$(printf '%s_%s' table agent).pid"

for CANDIDATE_PID_FILE in "$PID_FILE" "$LEGACY_PID_FILE"; do
    if [[ -f "$CANDIDATE_PID_FILE" ]]; then
        PID="$(tr -d '[:space:]' < "$CANDIDATE_PID_FILE")"
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            if [[ "$CANDIDATE_PID_FILE" != "$PID_FILE" ]]; then
                printf '%s\n' "$PID" > "$PID_FILE"
            fi
            echo "hall_agent is running (PID $PID)"
            exit 0
        fi
        rm -f "$CANDIDATE_PID_FILE"
    fi
done

echo "hall_agent is not running"
exit 1
