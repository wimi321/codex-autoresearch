#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e . pytest
autore init --force --preset auto
autore doctor

echo
echo "Next: edit autoresearch.toml if needed, then run:"
echo "  . .venv/bin/activate && autore run --iterations 5"
