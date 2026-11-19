# ADR-0002: SHA-256 in object Metadata, not ETag as a checksum

Status: accepted

## Context

We need to know if an object in S3 is byte-identical to
the source file.

## Decision

Store a real SHA-256 in object Metadata and Tagging, and
verify by re-hashing the actual object bytes. Never use
the S3 ETag as a checksum.

## Consequences

- Good: the check is correct for multipart uploads, where
  ETag is not a hash of the file.
- Good: a corrupted object is detected and repaired, not
  skipped because a manifest said it was already there.
- Bad: verify downloads every object, so it is slower
  than trusting a manifest. That is the point: integrity
  over speed.
