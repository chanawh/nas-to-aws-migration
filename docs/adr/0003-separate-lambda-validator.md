# ADR-0003: Separate Lambda validator, not verification inside the sync

Status: accepted

## Context

The sync copies files. Something must check that copy,
and the check must not share the sync's bugs.

## Decision

A separate Lambda function, triggered by S3 ObjectCreated
via SQS, re-downloads each object, re-hashes it, compares
it to the Metadata SHA-256, and writes the verdict to
DynamoDB. The CLI verify command is a third, independent
check, run by hand.

## Consequences

- Good: a bug in the sync cannot also hide in the check.
- Good: validation happens per object, as it lands, not
  only at the end of a run.
- Bad: three places now know about SHA-256, the sync,
  the Lambda, and the CLI verify. They are kept in sync
  by tests, not by sharing code, because the Lambda is
  a separate deployment unit.
