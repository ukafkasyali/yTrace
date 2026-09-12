#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

current_session="timef-rationale-v5b"
current_run="runs/llama-har-sp-timef-rationale-v5b"
next_run="runs/llama-har-sp-timef-rationale-joint-permutation-v6"
control_run="runs/llama-har-sp-timef-rationale-focused-control-v6"
v5_eval="artifacts/evaluation/v5b-grounding-validation-512"
next_eval="artifacts/evaluation/v6-joint-permutation-grounding-validation-512"
control_eval="artifacts/evaluation/v6-focused-control-grounding-validation-512"

for target in "$next_run" "$control_run" "$v5_eval" "$next_eval" "$control_eval"; do
  if [[ -e "$target" ]]; then
    echo "Refusing to overwrite $target" >&2
    exit 1
  fi
done

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
print(f"V5b complete at step {status.get('global_step')}; starting matched validation audit.")
PY

PYTHONPATH=src .venv/bin/python scripts/evaluate_grounding_panel.py \
  --checkpoint "$current_run/best_model.pt" \
  --prepared-root data/prepared/timef-v1 \
  --output "$v5_eval" \
  --samples 512 \
  --batch-size 4

PYTHONPATH=src .venv/bin/python -m robot_observability.train_opentslm \
  --config configs/opentslm_sp_joint_permutation.yaml \
  --prepared-root data/prepared/timef-v1 \
  --output runs \
  --run-name llama-har-sp-timef-rationale-joint-permutation-v6 \
  2>&1 | tee logs/timef-rationale-joint-permutation-v6.log

PYTHONPATH=src .venv/bin/python scripts/evaluate_grounding_panel.py \
  --checkpoint "$next_run/best_grounding_model.pt" \
  --prepared-root data/prepared/timef-v1 \
  --output "$next_eval" \
  --samples 512 \
  --batch-size 4

PYTHONPATH=src .venv/bin/python -m robot_observability.train_opentslm \
  --config configs/opentslm_sp_focused_control.yaml \
  --prepared-root data/prepared/timef-v1 \
  --output runs \
  --run-name llama-har-sp-timef-rationale-focused-control-v6 \
  2>&1 | tee logs/timef-rationale-focused-control-v6.log

PYTHONPATH=src .venv/bin/python scripts/evaluate_grounding_panel.py \
  --checkpoint "$control_run/best_grounding_model.pt" \
  --prepared-root data/prepared/timef-v1 \
  --output "$control_eval" \
  --samples 512 \
  --batch-size 4
