#!/usr/bin/env bash
set -euo pipefail

wait_session="${WAIT_FOR_TMUX_SESSION:-post-canary-v4}"
port="${QWEN_PORT:-8001}"
model="${QWEN_MODEL:-Qwen/Qwen3-VL-4B-Instruct}"
revision="${QWEN_REVISION:-ebb281ec70b05090aa6165b016eac8ec08e71b17}"
output="${QWEN_OUTPUT:-artifacts/evaluation/qwen3-vl-4b-bf16-plot-test}"
status_path="${QWEN_STATUS:-qwen3-vl-benchmark-status.json}"
server_log="${QWEN_SERVER_LOG:-qwen3-vl-server.log}"

write_status() {
  local state="$1"
  printf '{"state":"%s","timestamp":"%s"}\n' "$state" "$(date -Is)" >"${status_path}.tmp"
  mv "${status_path}.tmp" "$status_path"
}

write_status waiting_for_gpu
while tmux has-session -t "$wait_session" 2>/dev/null; do
  sleep 15
done

write_status starting_server
.vlm-venv/bin/vllm serve "$model" \
  --revision "$revision" \
  --served-model-name "$model" \
  --port "$port" \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.65 \
  >"$server_log" 2>&1 &
server_pid=$!

cleanup() {
  if kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

write_status waiting_for_server
attempt=0
until curl -fsS --max-time 3 "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    write_status server_failed
    wait "$server_pid"
  fi
  attempt=$((attempt + 1))
  if ((attempt >= 180)); then
    write_status server_timeout
    exit 1
  fi
  sleep 5
done

resume_args=()
if [[ -f "$output/manifest.json" ]]; then
  resume_args=(--resume)
fi
write_status evaluating
.vlm-venv/bin/python scripts/eval_plot_vlm.py \
  --endpoint "http://127.0.0.1:$port" \
  --output "$output" \
  "${resume_args[@]}"
write_status complete
