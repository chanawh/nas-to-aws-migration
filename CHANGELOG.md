# Changelog

## v1.1.0, 2026-11-22

Scaled evidence: 10,024-object / 10.49 GB run through the
unmodified engine against Garage v1.3.0 (local disk-backed
S3 emulator; moto is in-memory and cannot hold 10 GB in a
small VM). Sync 776.07s, verify PASS zero mismatches, 10/10
corruptions detected, repair exactly 10, final verify PASS.
New: `scripts/run_scaled_demo.py`, `docs/SCALED_RUN.md`,
`evidence/scaled-run-10gb-2026-11-22.json` (incl. S3
list-price cost model, ~$0.30 first month for the dataset).

## v1.0.0, 2026-11-19

Final portfolio form. System design, five ADRs, SECURITY.md,
CONTRIBUTING.md, interview guide, roadmap, CI green on the
final commit, tagged `v1.0.0`.

## Earlier

Initial build, 2026-11-18. Verified live locally, CI added,
honest limits stated up front.
