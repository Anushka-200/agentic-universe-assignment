#!/usr/bin/env bash
# Load test on public_03: wall-clock per simulated day, peak RSS of the controller. Output: reports/load_public03.txt
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs reports state
python -m sim serve --scenario scenarios/public_03_load.yaml --run-dir runs/mine03 --force --port 8703 > logs/serve03.log 2>&1 &
S=$!
for i in $(seq 1 60); do curl -sf localhost:8703/health >/dev/null && break; sleep 2; done
rm -f state/p03.db
START=$(date +%s)
CONTROLLER_DB=state/p03.db /usr/bin/time -v ./run.sh http://127.0.0.1:8703 > logs/controller03.log 2> logs/controller03.time
END=$(date +%s)
{
  echo "public_03_load: total wall-clock $((END-START)) s for $(curl -s localhost:8703/sim/state | python -c 'import json,sys;print(json.load(sys.stdin)["end_minute"]//1440)') simulated days"
  grep -E "Maximum resident|Elapsed" logs/controller03.time
  echo "per-day completion timestamps (wall clock, sim day):"
  grep "day done" logs/controller03.log | awk '{print "  "$1" "$2"  "$5}'
  python - <<'PY'
import sqlite3
db = sqlite3.connect("state/p03.db")
print("events ingested (call rows aggregated):", db.execute("select sum(dials) from stats where dim='voice'").fetchone()[0])
print("decisions logged:", db.execute("select count(*) from decisions").fetchone()[0])
print("proposals:", db.execute("select status,count(*) from proposals group by 1").fetchall())
PY
} > reports/load_public03.txt
kill $S
