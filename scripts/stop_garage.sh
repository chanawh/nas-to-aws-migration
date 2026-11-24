#!/usr/bin/env bash
# Stop the Garage server started by scripts/start_garage.sh.
set -euo pipefail
GARAGE_HOME="${GARAGE_HOME:-$HOME/workspace/nas2aws-ceiling/garage}"
if [ -f "$GARAGE_HOME/server.pid" ]; then
  kill "$(cat "$GARAGE_HOME/server.pid")" 2>/dev/null || true
  rm -f "$GARAGE_HOME/server.pid"
  echo "garage stopped"
else
  echo "no garage pid file at $GARAGE_HOME/server.pid"
fi
