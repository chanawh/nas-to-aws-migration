#!/usr/bin/env bash
# Unmount the OneFS shares mounted by mount_onefs.sh.
set -euo pipefail
MOUNT_ROOT="${1:-/mnt/onefs}"
for share in finance it-support it-operations operations; do
  sudo umount "$MOUNT_ROOT/$share" 2>/dev/null || true
done
echo "Unmounted."
