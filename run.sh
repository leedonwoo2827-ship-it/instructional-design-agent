#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  echo "[error] .venv not found. Run ./setup.sh first."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 자체 복구: venv 가 FastAPI 전환 이전(Streamlit 시절)일 수 있다.
if ! python -c "import fastapi, uvicorn" 2>/dev/null; then
  echo "[setup] Installing server dependencies (first run after update)..."
  python -m pip install -q --disable-pip-version-check -e . || {
    echo "[error] Dependency install failed. Run ./setup.sh and check the messages."
    exit 1
  }
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PORT="${IDA_PORT:-8701}"
echo "[run] http://localhost:$PORT  (Ctrl+C to stop)"
exec python -m uvicorn server:app --host 127.0.0.1 --port "$PORT"
