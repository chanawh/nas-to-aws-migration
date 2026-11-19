"""ACL mapping: source NAS permissions -> AWS identity.

S3 has no SMB ACLs. The mapping is stored two ways: as object
Metadata/Tags (owner, nas-group) on every uploaded object, and as
acl.json at the source root as the audit trail. This module validates
the mapping before any sync runs: every owner must exist in users.csv,
every NAS group must be a department or a MemberOf group, and every
share must name a non-empty aws_group.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def load_users(users_csv: Path) -> dict[str, dict]:
    users: dict[str, dict] = {}
    with users_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            users[row["SamAccountName"]] = row
    return users


def load_acl(acl_json: Path) -> dict[str, dict]:
    return json.loads(acl_json.read_text(encoding="utf-8"))


def validate_acl(users_csv: Path, acl_json: Path) -> list[str]:
    problems: list[str] = []
    users = load_users(users_csv)
    acl = load_acl(acl_json)
    known_groups: set[str] = set()
    for u in users.values():
        if (u.get("Department") or "").strip():
            known_groups.add(u["Department"].strip())
        for g in (u.get("MemberOf") or "").split(";"):
            if g.strip():
                known_groups.add(g.strip())
    for share, entry in sorted(acl.items()):
        if entry.get("owner") not in users:
            problems.append(f"{share}: owner {entry.get('owner')!r} not in users.csv")
        if entry.get("group") not in known_groups:
            problems.append(f"{share}: group {entry.get('group')!r} not a department or MemberOf group")
        if not entry.get("aws_group"):
            problems.append(f"{share}: aws_group is empty")
        if entry.get("mode") not in ("rwx", "r-x", "r--"):
            problems.append(f"{share}: mode {entry.get('mode')!r} is not rwx, r-x or r--")
    return problems
