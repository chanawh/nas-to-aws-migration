#!/usr/bin/env bash
# Start Azurite (Blob emulator) on 127.0.0.1:10000 with blob versioning ON,
# then smoke-test versioning and fail loudly if it is off.
#
# Versioning is CLI-flag-only in Azurite: Set Blob Service Properties
# accepts the flag but silently drops it, so the flag below must be
# present on every start. A stale data dir started without the flag
# runs unversioned with no warning - the smoke test catches that.
#
# Usage: bash scripts/start_azurite.sh
#   then run the Blob engine/tests/drills against http://127.0.0.1:10000
# Stop: kill $(cat /tmp/azurite/azurite.pid)  (or Ctrl-C if run in foreground)
# Env overrides: AZURITE_DIR (default ~/workspace/azurite),
#   AZURITE_DATA (default /tmp/azurite-data), AZURITE_VENV
#   (default ~/workspace/nas-to-aws-migration-venv-azure).
set -euo pipefail

AZURITE_DIR="${AZURITE_DIR:-$HOME/workspace/azurite}"
AZURITE_DATA="${AZURITE_DATA:-/tmp/azurite-data}"
AZURITE_VENV="${AZURITE_VENV:-$HOME/workspace/nas-to-aws-migration-venv-azure}"
HOST="127.0.0.1"
PORT="10000"
PID_FILE="/tmp/azurite/azurite.pid"
LOG_FILE="/tmp/azurite/azurite.log"

mkdir -p "$(dirname "$PID_FILE")" "$AZURITE_DATA"

# 1. Install Azurite via npm if missing.
if [ ! -x "$AZURITE_DIR/node_modules/.bin/azurite-blob" ]; then
  echo "Azurite not found in $AZURITE_DIR - installing azurite@3.38.0 ..."
  mkdir -p "$AZURITE_DIR"
  (cd "$AZURITE_DIR" && npm install azurite@3.38.0)
fi

# 2. Start it unless it is already answering.
if curl -fsS --max-time 2 "http://$HOST:$PORT/" >/dev/null 2>&1; then
  echo "Azurite already answering on $HOST:$PORT - not starting a second instance."
elif curl -s --max-time 2 "http://$HOST:$PORT/" >/dev/null 2>&1; then
  echo "Azurite already answering on $HOST:$PORT - not starting a second instance."
else
  echo "Starting Azurite (blob versioning ON) on $HOST:$PORT, data in $AZURITE_DATA ..."
  nohup "$AZURITE_DIR/node_modules/.bin/azurite-blob" \
    --blobHost "$HOST" --blobPort "$PORT" --location "$AZURITE_DATA" \
    --accountConfigAsJson '{"isBlobVersioningEnabled":true}' \
    >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Azurite pid $(cat "$PID_FILE"), log $LOG_FILE"
fi

# 3. Wait until it answers (first probe can race startup).
for i in $(seq 1 30); do
  if curl -s --max-time 2 "http://$HOST:$PORT/" >/dev/null 2>&1; then
    echo "Azurite answering after ~${i}s."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Azurite did not answer on $HOST:$PORT within 30s. See $LOG_FILE" >&2
    exit 1
  fi
  sleep 1
done

# 4. Smoke-test versioning: upload twice, expect 2 versions listed.
#    Fails loudly if versioning is off (e.g. started without the flag).
PY="$AZURITE_VENV/bin/python"
if [ ! -x "$PY" ]; then
  echo "ERROR: Azure venv python not found at $PY - create it first:" >&2
  echo "  python3 -m venv ~/workspace/nas-to-aws-migration-venv-azure" >&2
  echo "  ~/workspace/nas-to-aws-migration-venv-azure/bin/pip install 'azure-storage-blob==12.31.1'" >&2
  exit 1
fi
"$PY" - "$HOST" "$PORT" <<'PYEOF'
import sys
from azure.storage.blob import BlobServiceClient

host, port = sys.argv[1], sys.argv[2]
conn = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    f"BlobEndpoint=http://{host}:{port}/devstoreaccount1;"
)
svc = BlobServiceClient.from_connection_string(conn)
container = "smoke-versioning"
cc = svc.get_container_client(container)
try:
    cc.create_container()
except Exception:
    pass
bc = cc.get_blob_client("smoke.txt")
bc.upload_blob(b"v1", overwrite=True)
bc.upload_blob(b"v2", overwrite=True)
versions = [b for b in cc.list_blobs(include=["versions"]) if b.name == "smoke.txt"]
cc.delete_container()
if len(versions) != 2:
    print(f"ERROR: blob versioning smoke test FAILED: expected 2 versions, "
          f"listed {len(versions)}. Azurite was probably started WITHOUT "
          f"the --accountConfigAsJson versioning flag. Stop it, delete the "
          f"stale data dir, and restart via this script.", file=sys.stderr)
    sys.exit(1)
print("Versioning smoke test PASSED: 2 versions listed for smoke.txt.")
PYEOF
echo "Azurite ready on http://$HOST:$PORT (blob versioning ON)."
