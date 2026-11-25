#!/usr/bin/env bash
# Start Garage v1.3.0 (local disk-backed S3 emulator) for the
# throughput benchmark and the Garage drills.
#
# Downloads the single Garage binary if missing, starts the
# server, assigns the layout, imports an access key with a
# generated secret, and creates the four migration buckets.
# The secret is generated into $GARAGE_HOME/credentials.env
# (chmod 600) and never printed. Source that file to get
# S3_ENDPOINT / AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY:
#
#   bash scripts/start_garage.sh
#   source "$GARAGE_HOME/credentials.env"   # or see the path it prints
#
# Data lives in $GARAGE_HOME (default ~/workspace/nas2aws-ceiling/garage).
# Stop with scripts/stop_garage.sh. Delete $GARAGE_HOME to start over.
set -euo pipefail
GARAGE_HOME="${GARAGE_HOME:-$HOME/workspace/nas2aws-ceiling/garage}"
GARAGE_VERSION="${GARAGE_VERSION:-v1.3.0}"
mkdir -p "$GARAGE_HOME/meta" "$GARAGE_HOME/data"
cd "$GARAGE_HOME"

if [ ! -x ./garage ]; then
  echo "downloading Garage $GARAGE_VERSION..."
  curl -sO "https://garagehq.deuxfleurs.fr/_releases/$GARAGE_VERSION/x86_64-unknown-linux-musl/garage"
  chmod +x garage
fi

if [ ! -f garage.toml ]; then
  RPC_SECRET=$(openssl rand -hex 32)
  cat > garage.toml <<EOF
metadata_dir = "$GARAGE_HOME/meta"
data_dir = "$GARAGE_HOME/data"
db_engine = "sqlite"
replication_mode = "none"
rpc_bind_addr = "127.0.0.1:3901"
rpc_secret = "$RPC_SECRET"
[s3_api]
s3_region = "us-west-2"
api_bind_addr = "127.0.0.1:3900"
root_domain = ".s3.garage"
[admin]
api_bind_addr = "127.0.0.1:3903"
EOF
fi

if ./garage -c garage.toml status >/dev/null 2>&1; then
  echo "garage is already running"
elif [ ! -f server.pid ] || ! kill -0 "$(cat server.pid)" 2>/dev/null; then
  ./garage -c garage.toml server > server.log 2>&1 &
  echo $! > server.pid
  echo "garage starting (pid $(cat server.pid))..."
  for _ in $(seq 1 30); do
    if ./garage -c garage.toml status >/dev/null 2>&1; then break; fi
    sleep 1
  done
fi

NODE_ID=$(./garage -c garage.toml status 2>/dev/null | awk '$1 ~ /^[0-9a-f]{16}$/ {print $1; exit}')
if ! ./garage -c garage.toml layout show 2>/dev/null | grep -q "dc1"; then
  ./garage -c garage.toml layout assign -z dc1 -c 30G "$NODE_ID" >/dev/null
  ./garage -c garage.toml layout apply --version 1 >/dev/null
fi

if [ ! -f credentials.env ]; then
  KEY_ID="GK$(openssl rand -hex 12)"
  KEY_SECRET=$(openssl rand -hex 32)
  ./garage -c garage.toml key import --yes -n bench "$KEY_ID" "$KEY_SECRET" >/dev/null
  for s in finance it-support it-operations operations; do
    ./garage -c garage.toml bucket create "nas2aws-migration-$s" >/dev/null 2>&1 || true
    ./garage -c garage.toml bucket allow "nas2aws-migration-$s" --key bench --read --write --owner >/dev/null
  done
  cat > credentials.env <<EOF
export S3_ENDPOINT=http://127.0.0.1:3900
export AWS_ACCESS_KEY_ID=$KEY_ID
export AWS_SECRET_ACCESS_KEY=$KEY_SECRET
export S3_BACKEND_LABEL="Garage v1.3.0 (local disk-backed emulator)"
EOF
  chmod 600 credentials.env
fi

echo "Garage is up on http://127.0.0.1:3900"
echo "To use it: source $GARAGE_HOME/credentials.env"
