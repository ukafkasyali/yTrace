#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

current_session="timef-rationale-v5b"
current_run="runs/llama-har-sp-timef-rationale-v5b"
next_run="runs/llama-har-sp-timef-rationale-joint-permutation-v6"

while tmux has-session -t "$current_session" 2>/dev/null; do
  sleep 30
done

python3 - "$current_run/status.json" <<'PY'
import json
import sys
from pathlib import Path

status = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if status.get("state") != "complete":
    raise SystemExit(f"Refusing follow-on run: V5b did not complete cleanly: {status}")
print(f"V5b complete at step {status.get('global_step')}; launching joint-permutation V6.")
PY

if [[ -e "$next_run" ]]; then
  echo "Refusing to overwrite $next_run" >&2
  exit 1
fi

PYTHONPATH=src .venv/bin/python -m robot_observability.train_opentslm \
  --config configs/opentslm_sp_joint_permutation.yaml \
  --prepared-root data/prepared/timef-v1 \
  --output runs \
  --run-name llama-har-sp-timef-rationale-joint-permutation-v6 \
  2>&1 | tee logs/timef-rationale-joint-permutation-v6.log
