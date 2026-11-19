"""Quotas: per-share byte caps, loaded from JSON, checked before uploading."""
from __future__ import annotations

import json
from pathlib import Path


def load_quotas(path: Path | None) -> dict[str, int]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data.items()}


def usage_by_share(source_root: Path) -> dict[str, int]:
    usage: dict[str, int] = {}
    for share_dir in sorted(p for p in source_root.iterdir() if p.is_dir()):
        usage[share_dir.name] = sum(
            p.stat().st_size for p in share_dir.rglob("*") if p.is_file()
        )
    return usage


def check_quotas(source_root: Path, quotas: dict[str, int]) -> dict[str, dict]:
    usage = usage_by_share(source_root)
    return {
        share: {"bytes": usage.get(share, 0), "quota": quota, "over": usage.get(share, 0) > quota}
        for share, quota in quotas.items()
    }
