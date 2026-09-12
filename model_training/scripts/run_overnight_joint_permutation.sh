#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

current_session="timef-rationale-v5b"
current_run="runs/llama-har-sp-timef-rationale-v5b"
next_run="runs/llama-har-sp-timef-rationale-focused-v6"
v5_eval="artifacts/evaluation/v5b-conversational-validation-512"
next_eval="artifacts/evaluation/v6-conversational-validation-512"

for target in "$next_run" "$v5_eval" "$next_eval"; do
  if [[ -e "$target" ]]; then
    echo "Refusing to overwrite $target" >&2
    exit 1
  fi
done

wait_started=$(date +%s)
last_progress=$wait_started
last_step=-1
watchdog_triggered=0
while tmux has-session -t "$current_session" 2>/dev/null; do
  now=$(date +%s)
  step=$(python3 - "$current_run/status.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    print(int(json.loads(path.read_text(encoding="utf-8")).get("global_step", -1)))
except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
    print(-1)
PY
)
  if ((step > last_step)); then
    echo "V5b watchdog: progress step $step at $(date --iso-8601=seconds)."
    last_step=$step
    last_progress=$now
  fi
  if ((now - wait_started >= 3600 || now - last_progress >= 1200)); then
    echo "V5b watchdog: terminating a stuck session at step $step." >&2
    tmux kill-session -t "$current_session"
    watchdog_triggered=1
    break
  fi
  sleep 30
done

python3 - "$current_run/status.json" "$watchdog_triggered" <<'PY'
import json
import sys
from pathlib import Path

status = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
watchdog_triggered = bool(int(sys.argv[2]))
checkpoint = Path(sys.argv[1]).parent / "best_model.pt"
if status.get("state") != "complete" and not (watchdog_triggered and checkpoint.exists()):
    raise SystemExit(f"Refusing follow-on run: V5b did not complete cleanly: {status}")
print(
    f"V5b released the supervisor at step {status.get('global_step')} "
    f"(state={status.get('state')}, watchdog={watchdog_triggered}); starting validation audit."
)
PY

PYTHONPATH=src .venv/bin/python scripts/evaluate_grounding_panel.py \
  --checkpoint "$current_run/best_model.pt" \
  --prepared-root data/prepared/timef-v1 \
  --output "$v5_eval" \
  --samples 512 \
  --batch-size 4 \
  --selection event-intent-stratified

PYTHONPATH=src .venv/bin/python -m robot_observability.train_opentslm \
  --config configs/opentslm_sp_focused_control.yaml \
  --prepared-root data/prepared/timef-v1 \
  --output runs \
  --run-name llama-har-sp-timef-rationale-focused-v6 \
  2>&1 | tee logs/timef-rationale-focused-v6.log

PYTHONPATH=src .venv/bin/python scripts/evaluate_grounding_panel.py \
  --checkpoint "$next_run/best_grounding_model.pt" \
  --prepared-root data/prepared/timef-v1 \
  --output "$next_eval" \
  --samples 512 \
  --batch-size 4 \
  --selection event-intent-stratified
