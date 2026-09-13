#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"}
PREPARED_ROOT=${PREPARED_ROOT:-"${PROJECT_ROOT}/data/prepared/v1"}
OUTPUT_ROOT=${OUTPUT_ROOT:-"${PROJECT_ROOT}/artifacts/evaluation/qwen3-vl-4b-one-shot"}
PYTHON_BIN=${PYTHON_BIN:-"${PROJECT_ROOT}/.venv/bin/python"}
VLLM_BIN=${VLLM_BIN:-"${PROJECT_ROOT}/.vlm-venv/bin/vllm"}
MODEL=${MODEL:-"Qwen/Qwen3-VL-4B-Instruct"}
PORT=${PORT:-8010}
POLL_SECONDS=${POLL_SECONDS:-60}
IDLE_CONFIRMATIONS=${IDLE_CONFIRMATIONS:-2}
LIMIT=${LIMIT:-512}
LOG_ROOT=${LOG_ROOT:-"${PROJECT_ROOT}/runs/qwen3-vl-one-shot-launcher"}

# vLLM can invoke sibling build tools (for example, ninja during FlashInfer JIT
# warmup). An absolute VLLM_BIN alone does not place those tools on PATH.
export PATH="$(dirname "${VLLM_BIN}"):${PATH}"

mkdir -p "${LOG_ROOT}" "$(dirname "${OUTPUT_ROOT}")"
LOG_PATH="${LOG_ROOT}/launcher.log"
SERVER_LOG_PATH="${LOG_ROOT}/vllm.log"
LOCK_PATH="${LOG_ROOT}/launcher.lock"

exec 9>"${LOCK_PATH}"
if ! flock -n 9; then
  printf 'A one-shot GPU watcher is already active.\n' >&2
  exit 1
fi

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" | tee -a "${LOG_PATH}"
}

if [[ -e "${OUTPUT_ROOT}" ]]; then
  log "Refusing to overwrite existing output: ${OUTPUT_ROOT}"
  exit 1
fi
if [[ ! -x "${PYTHON_BIN}" || ! -x "${VLLM_BIN}" ]]; then
  log "Python or vLLM executable is missing."
  exit 1
fi

idle_checks=0
log "Waiting for ${IDLE_CONFIRMATIONS} consecutive idle GPU checks."
while (( idle_checks < IDLE_CONFIRMATIONS )); do
  if nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits | grep -Eq '[0-9]'; then
    idle_checks=0
    log "GPU busy; checking again in ${POLL_SECONDS}s."
  else
    idle_checks=$((idle_checks + 1))
    log "GPU idle check ${idle_checks}/${IDLE_CONFIRMATIONS}."
  fi
  if (( idle_checks < IDLE_CONFIRMATIONS )); then
    sleep "${POLL_SECONDS}"
  fi
done

log "Starting ${MODEL} on port ${PORT}."
"${VLLM_BIN}" serve "${MODEL}" \
  --port "${PORT}" \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.70 \
  --limit-mm-per-prompt '{"image":2}' \
  >"${SERVER_LOG_PATH}" 2>&1 &
server_pid=$!

cleanup() {
  if kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}"
    wait "${server_pid}" || true
  fi
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 120); do
  if curl --fail --silent --max-time 3 "http://127.0.0.1:${PORT}/v1/models" >/dev/null; then
    log "vLLM is ready; starting one-shot evaluation."
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    log "vLLM exited before becoming ready; inspect ${SERVER_LOG_PATH}."
    exit 1
  fi
  sleep 10
done

if ! curl --fail --silent --max-time 3 "http://127.0.0.1:${PORT}/v1/models" >/dev/null; then
  log "Timed out waiting for vLLM readiness."
  exit 1
fi

PYTHONPATH="${PROJECT_ROOT}/src" "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/eval_plot_vlm.py" \
  --prepared-root "${PREPARED_ROOT}" \
  --output "${OUTPUT_ROOT}" \
  --endpoint "http://127.0.0.1:${PORT}" \
  --model "${MODEL}" \
  --limit "${LIMIT}" \
  --shots 1

log "One-shot evaluation complete: ${OUTPUT_ROOT}/metrics.json"
