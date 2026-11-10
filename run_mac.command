#!/bin/bash
# Double-click (or run: bash run_mac.command) to start AI Fishing.
# The first run installs everything it needs (needs Python 3.12+).
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up, this takes a minute..."
    if ! python3 -m venv .venv; then
        echo "Python 3.12 or newer is needed: https://www.python.org/downloads/"
        exit 1
    fi
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { echo "Setup failed - check the Python version above."; rm -rf .venv; exit 1; }
fi
.venv/bin/python ai_fishing.py "$@"
