#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e . pytest
autore start --iterations 1 --skip-branch

echo
echo "Next:"
echo "  1. inspect or edit autoresearch.toml if needed"
echo "  2. continue with: . .venv/bin/activate && autore start --resume"
