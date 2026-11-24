# ADR-0006: parallel sync with a worker pool

Date: 2026-11-24. Status: accepted.

## Context

The single-threaded sync engine measured 13.5 MB/s on the
10 GB run (10,024 objects, 776s). At that rate a 1 TB run takes
~20 hours, so parallelism was the prerequisite for scale. The
engine's per-object work is I/O-bound (PUT, then a GET to
read back and re-hash), which is the shape a thread pool
helps.

## Decision

- `sync_one_share` / `sync_all` take `workers` (default 1,
  sequential, unchanged behavior) and an optional
  `client_factory`. With workers > 1, files in a share are
  uploaded by a `ThreadPoolExecutor`; each thread gets its own
  boto3 client from the factory, because a client must not be
  shared across threads for concurrent calls. Results are
  merged in sorted order so output is deterministic.
- Files >= 8 MiB upload via manual multipart (8 MiB parts,
  aborted on failure) instead of one huge PUT. Files below
  the threshold use put_object as before.
- Everything else is unchanged: the unchanged check still
  re-hashes the actual object bytes, the read-back
  verification still runs per file, quota is still checked
  before any upload, the manifest is still one object per
  bucket. Parallelism changes only how the per-file work is
  scheduled, never what counts as verified.
- The scan phase (SHA-256 of every source file) is still
  sequential. It is ~1s per 500 MB and was not the bottleneck
  at the measured sizes.

## Measured result (not the hoped-for result)

`evidence/throughput-2026-11-24.json`, 424 files / 401 MB,
Garage v1.3.0: median 17.57 MB/s at 1 worker, 19.77 MB/s at
4 workers (**1.13x**), 17.32 MB/s at 8 workers (~1.0x, noisy:
20.37 then 14.27 MB/s). The bottleneck is not client-side
parallelism on this emulator; it is the emulator itself plus
the per-object read-back. The engine is kept because it is
correct (parallel-vs-sequential equivalence is tested) and
because on real S3 the bottleneck moves to the network,
where parallelism historically helps — but that is a claim
for a real-S3 measurement, not for this ADR.

## Consequences

- `--workers` on the CLI (`python -m migrate_s3.cli sync
  --workers 4`).
- Multipart uploads are now part of the engine; the
  interrupted-multipart drill (`scripts/drills/drill_multipart.py`)
  proves an abandoned upload leaves no visible object and a
  resumed upload verifies.
- Next levers, in order: parallelize the scan phase, then
  measure against real S3.
