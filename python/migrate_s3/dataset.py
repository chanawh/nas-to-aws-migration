"""Synthetic source NAS dataset. Same dataset as the Azure twin.

Same 4 shares, same 12 users, same shape of data, 24 files, ~1.16 MB.
The production story is 80 TB / 400+ users; the lab is a scale model.
The sync engine cannot tell the difference.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

USERS = [
    ("a.chan", "Andrew Chan", "IT Support", "True", "2026-11-17", ""),
    ("j.smith", "Jane Smith", "Finance", "True", "2026-08-10", ""),
    ("m.lee", "Mike Lee", "IT Support", "True", "2026-11-16", ""),
    ("r.jones", "Rob Jones", "Operations", "False", "2026-06-01", ""),
    ("s.patel", "Sara Patel", "Finance", "True", "2026-11-15", ""),
    ("t.nguyen", "Tom Nguyen", "IT Support", "True", "2026-11-16", ""),
    ("k.brown", "Kevin Brown", "IT Operations", "True", "2026-05-01", "IT-Operations;Domain Admins"),
    ("l.garcia", "Lisa Garcia", "IT Support", "True", "2026-11-10", ""),
    ("d.kim", "David Kim", "IT Support", "True", "2026-08-18", ""),
    ("h.ali", "Hana Ali", "IT Support", "True", "2026-11-14", ""),
    ("p.chen", "Paul Chen", "Finance", "True", "2026-07-20", ""),
    ("e.wilson", "Emma Wilson", "IT Operations", "True", "2026-11-12", ""),
]

SHARES = {
    "finance": ["Q3-close-checklist.txt", "budget-2027-draft.csv", "vendor-invoices.txt"],
    "it-support": ["ticket-export.csv", "oncall-rota.txt", "runbook-index.md"],
    "it-operations": ["backup-schedule.txt", "privileged-access-log.csv", "change-log.md"],
    "operations": ["shift-plan.txt", "safety-briefing.md"],
}

# NAS group -> AWS identity. S3 has no SMB ACLs, so the mapping is
# stored as object tags (owner, group) plus this file as the audit trail.
# On real AWS the group would be an IAM group with a bucket policy.
ACL = {
    "finance": {"owner": "s.patel", "group": "Finance", "mode": "rwx", "aws_group": "nas2aws-finance"},
    "it-support": {"owner": "a.chan", "group": "IT Support", "mode": "rwx", "aws_group": "nas2aws-it-support"},
    "it-operations": {"owner": "k.brown", "group": "IT-Operations", "mode": "rwx", "aws_group": "nas2aws-it-operations"},
    "operations": {"owner": "r.jones", "group": "Operations", "mode": "r-x", "aws_group": "nas2aws-operations"},
}

QUOTAS = {
    "finance": 2 * 1024 * 1024,
    "it-support": 2 * 1024 * 1024,
    "it-operations": 1 * 1024 * 1024,
    "operations": 1 * 1024 * 1024,
}


def _content(seed: str, size_kb: int) -> bytes:
    rnd = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))
    line = f"synthetic file {seed} - line\n".encode()
    out = bytearray()
    while len(out) < size_kb * 1024:
        out += line
        out += str(rnd.randint(0, 999999)).encode() + b"\n"
    return bytes(out[: size_kb * 1024])


def generate_dataset(root: Path, *, seed: int = 7) -> dict:
    if root.exists():
        raise FileExistsError(f"{root} already exists - delete it first, never overwrite a source in place")
    root.mkdir(parents=True)
    rnd = random.Random(seed)
    total_files = 0
    total_bytes = 0
    (root / "users.csv").write_text(
        "SamAccountName,DisplayName,Department,Enabled,LastLogonDate,MemberOf\n"
        + "\n".join(",".join(u) for u in USERS)
        + "\n",
        encoding="utf-8",
    )
    (root / "acl.json").write_text(json.dumps(ACL, indent=2), encoding="utf-8")
    (root / "quotas.json").write_text(json.dumps(QUOTAS, indent=2), encoding="utf-8")
    for share, names in SHARES.items():
        share_dir = root / share
        share_dir.mkdir()
        for name in names:
            size_kb = rnd.choice([4, 16, 64, 128, 256])
            data = _content(f"{share}/{name}", size_kb)
            (share_dir / name).write_bytes(data)
            total_files += 1
            total_bytes += len(data)
        (share_dir / "empty-file.txt").write_bytes(b"")
        total_files += 1
        deep = share_dir / "archive" / "2024" / "q4" / "deep-file.txt"
        deep.parent.mkdir(parents=True, exist_ok=True)
        deep.write_bytes(_content(f"{share}/deep", 8))
        total_files += 1
        total_bytes += 8 * 1024
        spaced = share_dir / "file with spaces.txt"
        spaced.write_bytes(_content(f"{share}/spaced", 4))
        total_files += 1
        total_bytes += 4 * 1024
    stale = root / "it-operations" / "stale-200d-backup-manifest.txt"
    stale.write_bytes(_content("stale-200d", 32))
    total_files += 1
    total_bytes += 32 * 1024
    return {"files": total_files, "bytes": total_bytes, "shares": sorted(SHARES)}
