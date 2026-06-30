#!/usr/bin/env bash
# Weekly voice-correction distiller (Linux cron parity with run_analyze.bat).
# Regenerates the auto-distilled half of voice-corrections.md from David's
# recent edits; his "My rules" section is never touched.
set -euo pipefail

# cd to the project root (this script lives in scripts/).
cd "$(dirname "$0")/.."

exec .venv/bin/python src/analyze_edits.py
