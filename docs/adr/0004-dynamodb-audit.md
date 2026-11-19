# ADR-0004: DynamoDB audit table, not just the S3 manifest

Status: accepted

## Context

We need an evidence trail for what was copied, when,
with which hash, and whether it passed validation.

## Decision

One DynamoDB item per object per sync, PK share, SK
object_key, with status, hash, size, and timestamp,
and a 90-day TTL. The S3 manifest object remains, but
only as the resume point for the sync.

## Consequences

- Good: one query on a share returns its whole audit.
- Good: PAY_PER_REQUEST costs $0 at lab scale.
- Bad: two sources of truth exist, the manifest and
  the table. They can disagree, and the table is the
  one that matters for evidence, the manifest is the
  one that matters for resume.
