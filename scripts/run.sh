#!/usr/bin/env bash
# Cron wrapper: run the monitor from the project root using its venv.
#
# Cron runs with a minimal PATH, so we make sure the venv Python (and the
# Node-based Claude Code CLI the Agent SDK shells out to) are reachable.
set -euo pipefail

# cd to the project root (this script lives in scripts/).
cd "$(dirname "$0")/.."

# Ensure node + the `claude` CLI are on PATH under cron.
# If you installed Node via nvm, add its bin dir here, e.g.:
#   export PATH="$HOME/.nvm/versions/node/v20.11.0/bin:$PATH"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

exec .venv/bin/python src/main.py
