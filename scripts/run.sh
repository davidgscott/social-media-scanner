#!/usr/bin/env bash
# Cron wrapper: run the monitor from the project root using its venv.
set -euo pipefail

# cd to the project root (this script lives in scripts/).
cd "$(dirname "$0")/.."

exec .venv/bin/python src/main.py
