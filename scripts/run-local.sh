#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCOUT_DIR="$ROOT_DIR/data_sourcing"
INGESTION_DIR="$ROOT_DIR/data_ingestion"
FRONTEND_DIR="$ROOT_DIR/frontend"
MODE="cached"
INSTALL_MODE="missing"
OPEN_BROWSER=false
PIDS=()

usage() {
  cat <<'EOF'
Usage: ./scripts/run-local.sh [options]

Start the dataset-scout API, ingestion API and worker, fixture inference bridge, and frontend.

Options:
  --cached        Use the checked-in collision dataset fixture (default; no API credits)
  --live          Use Tavily and OpenAI credentials from data_sourcing/.env
  --install       Reinstall/sync dependencies before starting
  --skip-install  Do not install dependencies, even when they appear to be missing
  --open          Open http://127.0.0.1:5173 after all services are ready
  -h, --help      Show this help

Examples:
  ./scripts/run-local.sh
  ./scripts/run-local.sh --live --open
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  local pid
  trap - EXIT INT TERM HUP
  if ((${#PIDS[@]})); then
    printf '\nStopping local services...\n'
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    for pid in "${PIDS[@]}"; do
      wait "$pid" 2>/dev/null || true
    done
  fi
}

port_is_busy() {
  local port="$1"
  command -v lsof >/dev/null 2>&1 &&
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local attempt
  for attempt in {1..120}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      fail "$name stopped before becoming ready"
    fi
    if curl --fail --silent --show-error --output /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
  done
  fail "$name did not become ready at $url"
}

open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url"
  else
    printf 'No browser opener found; visit %s manually.\n' "$url"
  fi
}

while (($#)); do
  case "$1" in
    --cached) MODE="cached" ;;
    --live) MODE="live" ;;
    --install) INSTALL_MODE="always" ;;
    --skip-install) INSTALL_MODE="never" ;;
    --open) OPEN_BROWSER=true ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown option: $1"
      ;;
  esac
  shift
done

for command_name in uv node npm curl; do
  command -v "$command_name" >/dev/null 2>&1 || fail "$command_name is required"
done

if port_is_busy 8000; then
  curl --fail --silent --show-error --output /dev/null "http://127.0.0.1:8000/api/health" 2>/dev/null ||
    fail "port 8000 is in use but does not serve the Trace inference health endpoint"
  REUSE_INFERENCE=true
else
  REUSE_INFERENCE=false
fi
port_is_busy 8001 && fail "port 8001 is already in use (dataset scout)"
port_is_busy 8002 && fail "port 8002 is already in use (dataset ingestion)"
port_is_busy 5173 && fail "port 5173 is already in use (frontend)"

if [[ "$INSTALL_MODE" == "always" ||
      ("$INSTALL_MODE" == "missing" && ! -x "$SCOUT_DIR/.venv/bin/python") ]]; then
  printf 'Syncing Python dependencies...\n'
  (cd "$SCOUT_DIR" && uv sync --all-groups)
elif [[ "$INSTALL_MODE" == "never" && ! -x "$SCOUT_DIR/.venv/bin/python" ]]; then
  fail "Python dependencies are missing; rerun without --skip-install"
fi

if [[ "$INSTALL_MODE" == "always" ||
      ("$INSTALL_MODE" == "missing" && ! -x "$INGESTION_DIR/.venv/bin/dataset-ingestion-api") ]]; then
  printf 'Installing ingestion dependencies...\n'
  if [[ ! -x "$INGESTION_DIR/.venv/bin/python" ]]; then
    uv venv "$INGESTION_DIR/.venv"
  fi
  uv pip install --python "$INGESTION_DIR/.venv/bin/python" --editable "$INGESTION_DIR"
elif [[ "$INSTALL_MODE" == "never" && ! -x "$INGESTION_DIR/.venv/bin/dataset-ingestion-api" ]]; then
  fail "ingestion dependencies are missing; rerun without --skip-install"
fi

if [[ "$INSTALL_MODE" == "always" ||
      ("$INSTALL_MODE" == "missing" && ! -x "$FRONTEND_DIR/node_modules/.bin/vite") ]]; then
  printf 'Installing frontend dependencies...\n'
  (cd "$FRONTEND_DIR" && npm ci)
elif [[ "$INSTALL_MODE" == "never" && ! -x "$FRONTEND_DIR/node_modules/.bin/vite" ]]; then
  fail "frontend dependencies are missing; rerun without --skip-install"
fi

if [[ "$MODE" == "live" ]]; then
  [[ -f "$SCOUT_DIR/.env" ]] || fail "data_sourcing/.env is required for --live"
  (
    cd "$SCOUT_DIR"
    uv run --offline python -c '
from data_sourcing.config import Settings

settings = Settings()
missing = [
    name
    for name, value in (
        ("SOURCING_TAVILY_API_KEY", settings.tavily_api_key),
        ("SOURCING_OPENAI_API_KEY", settings.openai_api_key),
        ("SOURCING_OPENAI_MODEL", settings.openai_model),
    )
    if not value
]
if missing:
    raise SystemExit("Missing live settings in data_sourcing/.env: " + ", ".join(missing))
if not settings.github_token:
    print("Warning: SOURCING_GITHUB_TOKEN is unset; GitHub rate limits may reduce verification coverage.")
'
  )
fi

trap cleanup EXIT INT TERM HUP

printf 'Starting dataset scout in %s mode...\n' "$MODE"
(
  cd "$SCOUT_DIR"
  if [[ "$MODE" == "cached" ]]; then
    export SOURCING_TAVILY_API_KEY=""
    export SOURCING_OPENAI_API_KEY=""
    export SOURCING_OPENAI_MODEL=""
    export SOURCING_GITHUB_TOKEN=""
  fi
  exec uv run --offline uvicorn data_sourcing.api:create_app \
    --factory --host 127.0.0.1 --port 8001
) &
SCOUT_PID="$!"
PIDS+=("$SCOUT_PID")
wait_for_url "dataset scout" "http://127.0.0.1:8001/docs" "$SCOUT_PID"

printf 'Starting dataset ingestion API and worker...\n'
(
  cd "$INGESTION_DIR"
  export INGESTION_SOURCING_API_URL="http://127.0.0.1:8001"
  export INGESTION_DATA_DIR="$INGESTION_DIR/var/ingestion"
  exec "$INGESTION_DIR/.venv/bin/dataset-ingestion-api"
) &
INGESTION_API_PID="$!"
PIDS+=("$INGESTION_API_PID")
wait_for_url "dataset ingestion" "http://127.0.0.1:8002/docs" "$INGESTION_API_PID"
(
  cd "$INGESTION_DIR"
  export INGESTION_SOURCING_API_URL="http://127.0.0.1:8001"
  export INGESTION_DATA_DIR="$INGESTION_DIR/var/ingestion"
  exec "$INGESTION_DIR/.venv/bin/dataset-ingestion-worker"
) &
INGESTION_WORKER_PID="$!"
PIDS+=("$INGESTION_WORKER_PID")

if [[ "$REUSE_INFERENCE" == "true" ]]; then
  printf 'Reusing the existing Trace inference service on port 8000.\n'
else
  printf 'Starting fixture inference bridge without model loading...\n'
  (
    cd "$ROOT_DIR"
    exec "$SCOUT_DIR/.venv/bin/python" -m inference.server --no-load-runtime
  ) &
  INFERENCE_PID="$!"
  PIDS+=("$INFERENCE_PID")
  wait_for_url "fixture inference bridge" "http://127.0.0.1:8000/api/health" "$INFERENCE_PID"
fi

printf 'Starting frontend...\n'
(
  cd "$FRONTEND_DIR"
  export VITE_API_BASE_URL="/api"
  exec npm run dev -- --port 5173
) &
FRONTEND_PID="$!"
PIDS+=("$FRONTEND_PID")
wait_for_url "frontend" "http://127.0.0.1:5173" "$FRONTEND_PID"

printf '\nReady: http://127.0.0.1:5173\n'
if [[ "$MODE" == "cached" ]]; then
  printf 'Mode: cached fixture (0 Tavily credits). Keep the default research brief in the UI.\n'
else
  printf 'Mode: live Tavily + OpenAI. Searches may consume provider credits.\n'
fi
if [[ "$REUSE_INFERENCE" == "false" ]]; then
  printf 'Fixture inference bridge active; model loading is disabled.\n'
fi
printf 'Press Ctrl-C to stop all local services.\n\n'

if [[ "$OPEN_BROWSER" == "true" ]]; then
  open_url "http://127.0.0.1:5173"
fi

while :; do
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || fail "a local service stopped unexpectedly"
  done
  sleep 1
done
