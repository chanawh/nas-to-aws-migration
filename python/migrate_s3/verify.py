"""Verify phase: download every object and re-hash it against the source.

Separate from sync on purpose. Sync trusts its own upload check;
verify trusts nothing - it re-reads both sides and reports missing,
extra, changed, and checksum-mismatched objects. A clean verify is
the evidence for the '100% checksum-verified' claim.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .sync import list_bucket_objects, scan_directory


@dataclass
class VerifyResult:
    checked: int = 0
    ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.missing and not self.mismatched

    @property
    def failed(self) -> list[str]:
        return self.missing + self.mismatched


def verify(s3, source: Path, bucket: str) -> VerifyResult:
    result = VerifyResult()
    src = scan_directory(source)
    tgt = list_bucket_objects(s3, bucket)
    result.checked = len(src)
    for rel, rec in src.items():
        if rel not in tgt:
            result.missing.append(rel)
            continue
        body = s3.get_object(Bucket=bucket, Key=rel)["Body"].read()
        if hashlib.sha256(body).hexdigest() != rec.sha256:
            result.mismatched.append(rel)
        else:
            result.ok.append(rel)
    for rel in sorted(tgt):
        if rel not in src:
            result.extra.append(rel)
    return result
