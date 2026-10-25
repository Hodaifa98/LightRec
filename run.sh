#!/usr/bin/env sh
# Create the virtual environment on first run, then start the app.
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
    echo "Setting up LightRec for the first time..."
    python3 -m venv .venv || { echo "Could not create a virtual environment (install python3-venv)."; exit 1; }
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi
exec .venv/bin/python LightRec.py
