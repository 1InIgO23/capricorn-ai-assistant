#!/bin/bash
# Starts Capri on macOS in the background, detached from the terminal.
# Double-click this file in Finder (or run ./run_capri.command); the Terminal
# window that opens can be closed right away, Capri keeps running.
cd "$(dirname "$0")" || exit 1
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi
nohup "$PYTHON" main.py >/dev/null 2>&1 &
disown
echo "Capri is starting. You can close this window."
