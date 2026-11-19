#!/usr/bin/env bash
# Start the moto S3 emulator on :5000. No Docker, no account, no token.
# Uses the project venv if it exists, else the system python.
# Usage: bash scripts/start_moto.sh
#   then in another shell, run the CLI with --endpoint-url http://localhost:5000
# Stop: kill the process, or Ctrl-C. State is in-memory and is lost on stop.
set -euo pipefail
VENV_BIN="$HOME/workspace/nas-to-aws-migration-venv/bin"
if [ -x "$VENV_BIN/moto_server" ]; then
  exec "$VENV_BIN/moto_server" -p 5000 -H 127.0.0.1
else
  exec moto_server -p 5000 -H 127.0.0.1
fi
