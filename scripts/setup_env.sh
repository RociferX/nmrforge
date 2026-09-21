#!/usr/bin/env bash
# Create the venv (nmrforge/) and install NMRForge (Linux development environment)
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv nmrforge
nmrforge/bin/python -m pip install --upgrade pip
nmrforge/bin/python -m pip install -e .
echo "environment ready: nmrforge/bin/python main.py"
