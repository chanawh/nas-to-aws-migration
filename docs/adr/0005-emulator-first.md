# ADR-0005: Emulator-first, real AWS gated

Status: accepted

## Context

The project must be buildable and testable for $0, and
also be the same code that runs on real AWS.

## Decision

LocalStack is the primary emulator, moto is the
no-Docker fallback, and the Terraform is the same code
for both, with the endpoint URL as the only swap point.
FSx, DataSync, and CloudTrail are gated off by default,
because they are not emulated for free, and enabling
them against an emulator would fail at apply time.

## Consequences

- Good: the whole project runs for $0, in CI, on every push.
- Good: one real AWS run, under $1 for a day without FSx,
  proves the same Terraform against real APIs.
- Bad: the emulator is not AWS. Moto's Lambda execution
  is mocked, LocalStack was not run live here, and
  FSx/DataSync were validated in Terraform only.
  Those limits are stated in the README, not hidden.
