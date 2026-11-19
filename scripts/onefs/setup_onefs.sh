#!/bin/sh
# Run ON the OneFS virtual node, as root, over SSH:
#   ssh root@<ONEFS_IP> 'sh -s' < scripts/onefs/setup_onefs.sh
# Or copy it to the node and run: sh setup_onefs.sh
# Creates the 4 SMB shares, lab permissions, and SmartQuotas that the
# NAS-to-AWS migration expects. Commands are OneFS 9.x isi CLI, from
# the Dell CLI Administration Guide. If a command fails on your OneFS
# version, do the same step in the WebUI (Protocols > Windows Sharing
# (SMB) > SMB Shares) and keep going - the share name and path are
# what the migration cares about, not which tool created them.
# Lab only: Everyone gets full access on these shares. Never do this
# on a production cluster.
set -eu

for share in finance it-support it-operations operations; do
  path="/ifs/data/$share"
  echo "== $share -> $path =="
  mkdir -p "$path"
  if isi smb shares view "$share" >/dev/null 2>&1; then
    echo "share $share already exists, skipping create"
  else
    isi smb shares create "$share" --path="$path" --create-path --browsable=true
  fi
  # Lab permission: Everyone full. The Dell guide form is:
  #   isi smb shares permission modify <share> --wellknown Everyone
  isi smb shares permission modify "$share" --wellknown Everyone || true
done

echo "== SmartQuotas (hard, enforced) =="
# Syntax: isi quota quotas create --path=<path> --type=directory
#   --hard-threshold=<size> --enforced=yes
for spec in "finance 2G" "it-support 2G" "it-operations 1G" "operations 1G"; do
  set -- $spec
  if isi quota quotas list --path="/ifs/data/$1" 2>/dev/null | grep -q "/ifs/data/$1"; then
    echo "quota on /ifs/data/$1 already exists, skipping"
  else
    isi quota quotas create --path="/ifs/data/$1" --type=directory --hard-threshold="$2" --enforced=yes || echo "quota create failed for $1 - set it in the WebUI (File System > SmartQuotas)"
  fi
done

echo "== verify =="
isi smb shares list
isi quota quotas list
echo "done. SMB shares are at \\\\<ONEFS_IP>\\finance, etc."
