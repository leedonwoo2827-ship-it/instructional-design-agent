#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "[error] .venv not found. Run ./setup.sh first."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PORT="${STREAMLIT_SERVER_PORT:-8701}"
streamlit run app.py --server.port "$PORT"
