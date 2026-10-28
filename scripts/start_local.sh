#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  echo 'Bitte zuerst .venv/bin/python scripts/configure_local.py ausführen.'
  exit 1
fi
exec .venv/bin/python scripts/run_local.py
