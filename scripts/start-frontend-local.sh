#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x /opt/homebrew/bin/python3.12 ]]; then
    PYTHON_BIN="/opt/homebrew/bin/python3.12"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "Cannot find Python. Ask Codex to check the local Python installation."
    exit 1
  fi
fi

echo "Starting Mac local frontend at http://127.0.0.1:5173"
echo "This starts the local frontend only. It does not deploy or restart Aliyun."

cd "$ROOT_DIR/frontend"
exec "$PYTHON_BIN" -m http.server 5173 --bind 127.0.0.1
