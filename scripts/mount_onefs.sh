#!/usr/bin/env bash
# Mount the 4 OneFS SMB shares on a Linux host.
# Requires cifs-utils: sudo apt install cifs-utils
# Usage: ONEFS_HOST=192.168.1.50 ONEFS_USER=root bash scripts/mount_onefs.sh /mnt/onefs
# Password is read from $ONEFS_PASSWORD, or prompted if unset.
# Unmount with: bash scripts/unmount_onefs.sh /mnt/onefs
set -euo pipefail
MOUNT_ROOT="${1:-/mnt/onefs}"
HOST="${ONEFS_HOST:?set ONEFS_HOST, e.g. 192.168.1.50}"
USER="${ONEFS_USER:-root}"
if [ -z "${ONEFS_PASSWORD:-}" ]; then
  read -rsp "Password for $USER@$HOST: " ONEFS_PASSWORD; echo
fi
mkdir -p "$MOUNT_ROOT"
for share in finance it-support it-operations operations; do
  mkdir -p "$MOUNT_ROOT/$share"
  sudo mount -t cifs "//$HOST/$share" "$MOUNT_ROOT/$share" \
    -o "vers=3.0,username=$USER,password=$ONEFS_PASSWORD,uid=$(id -u),gid=$(id -g)"
  echo "$share -> $MOUNT_ROOT/$share"
done
echo "Mounted. Test: ls $MOUNT_ROOT/finance"
