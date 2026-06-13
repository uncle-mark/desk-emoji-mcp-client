#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 was not found. Please install Python 3 and try again."
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Upgrading pip ..."
python -m pip install --upgrade pip

echo "Installing dependencies ..."
python -m pip install -r requirements.txt

echo "Checking tkinter ..."
python - <<'PY'
import tkinter  # noqa: F401
PY

echo "Starting Desk-Emoji MCP Client ..."
python app.py
