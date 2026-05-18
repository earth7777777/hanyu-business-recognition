#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

cd "$BACKEND_DIR"

if [[ ! -f .env ]]; then
  echo "Missing backend/.env. This file is local-only and is not stored in GitHub."
  echo "Ask Codex to recreate the local backend config before starting the backend."
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing backend/.venv. This is the local Python dependency folder."
  echo "Ask Codex to install backend dependencies before starting the backend."
  exit 1
fi

echo "Starting Mac local backend at http://127.0.0.1:8000"
echo "This starts the local backend only. It does not deploy or restart Aliyun."

set -a
source ./.env
set +a

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
