#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "[error] python3 not on PATH"; exit 1; }
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi
echo
echo "Setup complete. Run ./run.sh to start. Enter your LiteLLM API key in the app sidebar."
