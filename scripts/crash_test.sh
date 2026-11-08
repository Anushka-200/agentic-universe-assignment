#!/usr/bin/env bash
# Crash-recovery demo: start the controller, SIGKILL it after N seconds, restart, let it finish.
# Usage: scripts/crash_test.sh <SIM_URL> [seconds_before_kill=40]
# Afterwards, check: no proposal applied twice, no pending approval lost (python -m controller.audit --summary).
set -uo pipefail
URL="${1:?usage: scripts/crash_test.sh <SIM_URL> [seconds]}"; WAIT="${2:-40}"
cd "$(dirname "$0")/.."
mkdir -p logs
./run.sh "$URL" > logs/crash_run1.log 2>&1 &
PID=$!
sleep "$WAIT"
echo "[crash_test] sim state before kill: $(curl -s "$URL/sim/state")"
kill -9 "$PID"; wait "$PID" 2>/dev/null
echo "[crash_test] SIGKILLed controller pid $PID; restarting"
./run.sh "$URL" > logs/crash_run2.log 2>&1
echo "[crash_test] restart exit code: $?"
echo "[crash_test] sim state at end: $(curl -s "$URL/sim/state")"
