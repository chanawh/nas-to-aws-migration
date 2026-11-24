# Throughput: 1 worker vs N workers (2026-11-24)

The single-threaded engine measured 13.5 MB/s on the 10 GB run
(`evidence/scaled-run-10gb-2026-11-22.json`). At that rate a
1 TB run takes ~20 hours, so parallelism was the prerequisite
for scale. This benchmark measures the actual speedup on the
same dataset and backend.

## Method

`scripts/run_throughput.py`, 424 files / 420,593,664 bytes
(400 x 1 MB scale files + the 24-file base dataset), buckets
emptied between runs so every run is a full upload. Backend:
Garage v1.3.0, local disk-backed S3 emulator. Each run includes
the engine's read-back verification of every object, so the
numbers are upload + verify, not upload alone. Full verify
after the last run: PASS, 424 checked, 0 mismatched.

Evidence: `evidence/throughput-2026-11-24.json`.

## Results

| Workers | Run 1 | Run 2 | Median MB/s |
|---|---|---|---|
| 1 | 17.17 MB/s | 17.97 MB/s | 17.57 |
| 4 | 19.77 MB/s | — | 19.77 |
| 8 | 20.37 MB/s | 14.27 MB/s | 17.32 |

**Speedup: 1.13x at 4 workers, ~1.0x at 8 workers.** The
8-worker runs were noisy (20.37 then 14.27 MB/s), which is
itself a result: the bottleneck is not client-side upload
parallelism. The likely limiter is the emulator (single
Garage node, loopback) plus the per-object read-back, which
doubles the work and is sequential per file by design. The
parallel engine is still worth keeping: it is correct (25/25
tests, including parallel-vs-sequential equivalence and
multipart), and on real S3 the bottleneck moves to the
network, where parallelism historically helps. But the honest
claim is the measured one: on this emulator, parallelism buys
about 13%.

## What this means for scale

- A 1 TB run at the measured 17.6–19.8 MB/s still takes
  ~14–16 hours on the emulator. The parallel engine alone
  does not unlock it.
- The next levers, in order: parallelize the scan phase
  (SHA-256 of every file is still sequential), then measure
  against real S3, where the server is not the bottleneck.
- The 10 GB single-threaded run (13.5 MB/s) and these
  400 MB runs (17.6 MB/s) are consistent: same engine, same
  emulator, same order of magnitude.

## Reproduce

```sh
bash scripts/start_garage.sh
source ~/workspace/nas2aws-ceiling/garage/credentials.env
python scripts/run_throughput.py \
    --source <dir> --per-share 100 --file-mb 1 \
    --workers 1 1 4 8 8 --out evidence/throughput-<date>.json
```
