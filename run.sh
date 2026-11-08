#!/usr/bin/env bash
# Starts the outcome-first controller. Contract: ./run.sh <SIM_URL> runs until the scenario finishes.
# No Docker; needs Python 3.10+ with requirements.txt installed (only httpx is used by the controller).
# State/audit lives in state/controller.db (SQLite). Re-running against the same sim resumes safely;
# a fresh sim world gets a fresh DB (the old one is kept as *.old).
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-python}"
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" -m controller.main --url "$SIM_URL" --db "${CONTROLLER_DB:-state/controller.db}"
