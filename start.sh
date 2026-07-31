#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
VENV_PYTHON="$VENV_DIR/bin/python3"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 was not found. Please install Python 3 and try again."
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

if [ ! -x "$VENV_PYTHON" ]; then
  echo "Error: virtual environment Python was not found at $VENV_PYTHON."
  echo "Please remove $VENV_DIR and run this script again."
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Upgrading pip ..."
"$VENV_PYTHON" -m pip install --upgrade pip

echo "Installing dependencies ..."
"$VENV_PYTHON" -m pip install -r requirements.txt

echo "Checking CustomTkinter/tkinter runtime ..."
"$VENV_PYTHON" - <<'PY'
import tkinter  # noqa: F401
import customtkinter  # noqa: F401
PY

echo "Starting Desk-Emoji MCP Client ..."
"$VENV_PYTHON" app.py
