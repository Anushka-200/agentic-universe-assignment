#!/usr/bin/env bash
# Runs public_01 and public_02 end to end (fresh sims on :8702/:8700) and writes grade reports.
# Baselines must exist first: python -m sim baseline --scenario scenarios/public_0X_*.yaml --run-dir runs/base0X
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs reports state
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01 --force --port 8702 > logs/serve01.log 2>&1 &
S1=$!
python -m sim serve --scenario scenarios/public_02_incidents.yaml --run-dir runs/mine02 --force --port 8700 > logs/serve02.log 2>&1 &
S2=$!
for i in $(seq 1 30); do curl -sf localhost:8700/health >/dev/null && curl -sf localhost:8702/health >/dev/null && break; sleep 2; done
rm -f state/p01.db state/p02.db
CONTROLLER_DB=state/p01.db ./run.sh http://127.0.0.1:8702 > logs/controller01.log 2>&1 &
C1=$!
CONTROLLER_DB=state/p02.db ./run.sh http://127.0.0.1:8700 > logs/controller02.log 2>&1
wait $C1
python -m sim grade --run runs/mine01 --baseline runs/base01 --out reports/public_01.md > /dev/null
python -m sim grade --run runs/mine02 --baseline runs/base02 --out reports/public_02.md > /dev/null
kill $S1 $S2
