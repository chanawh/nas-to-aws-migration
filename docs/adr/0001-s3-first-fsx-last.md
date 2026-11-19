# ADR-0001: S3 as the landing zone, FSx as the working file system

Status: accepted

## Context

Files need to land somewhere durable and cheap, and also
need to be served over SMB/NFS to applications that cannot
use objects.

## Decision

S3 first, FSx last. S3 is the durable landing zone, with
versioning and Glacier lifecycle. FSx for NetApp ONTAP is
the working file system, built only on real AWS, gated off
in the emulator.

## Consequences

- Good: S3 is $0.023/GB/month, FSx is ~$0.12/GB/month,
  so the archive is cheap and only hot data pays FSx prices.
- Good: S3 works in the emulator, FSx does not, so the
  lab can run for $0.
- Bad: applications that need a file system cannot use
  S3 directly; they wait for the DataSync step.
- Bad: two copies of hot data exist, in S3 and in FSx,
  until the cutover is complete.
