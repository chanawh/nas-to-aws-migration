# EFS: the secondary migration target

S3 is the primary target. EFS is the file-system target for the
workloads that need POSIX semantics and cannot go to object
storage. Same dataset, same SHA-256 verification, different
destination.

## Design

- Terraform (`terraform/efs.tf`, gated by `enable_efs=false`,
  real-AWS-only): one encrypted EFS file system, General Purpose,
  Bursting throughput, transition to Infrequent Access after 30
  days, with mount targets in the **default VPC** (data sources,
  zero new network resources). EFS only needs subnets for mount
  targets; the default VPC has one per AZ. The FSx file creates
  its own VPC because FSx ONTAP needs a dedicated network; EFS
  does not. Lab choice: a production design would use a dedicated
  VPC with the same mount-target pattern. If the default VPC has
  been deleted in the account, the apply fails; the fix is a
  dedicated VPC (same resources, `vpc_id` from that VPC).
- Copy (`scripts/migrate_efs.py`): copies the dataset to the
  mount, per share, with the same per-share quota rule as the S3
  engine (an over-quota share is skipped whole, never half-copied)
  and the same manifest shape (`_manifest.json` per share), then
  a verify pass that re-hashes the copied files against the
  source. It reuses the engine's hashing (`scan_directory` /
  `compute_sha256` from `migrate_s3.sync`). It does not mount
  anything; the file system must already be mounted
  (`mount -t nfs4 -o nfsvers=4.1 <dns>:/ <mount-point>`, DNS name
  from `terraform output efs_dns_name`).
- The alternative is DataSync S3 -> EFS (a managed task from the
  share buckets to the file system). The script is the primary
  path because it is verifiable (unit-tested against a local
  directory) and its verification is explicit per-object SHA-256,
  the same as the S3 verify; DataSync's verification is its own
  POINT_IN_TIME_CONSISTENT check and cannot be tested in the lab.

## What is verified, and what is not

- The copy/verify logic is unit-tested (`python/tests/test_efs.py`,
  5 tests) against a local directory standing in for the mount.
- The backend label comes from `/proc/mounts`: a local directory
  is labelled `local directory stand-in (NOT EFS)`, never EFS.
- The real EFS mount is real-AWS-only and **not measured**. EFS
  is not emulated; enabling `enable_efs` against an emulator
  fails by design.

## Run it on real AWS

1. `terraform apply -var enable_efs=true` (after the base apply).
2. On an EC2 instance in the default VPC (or any host that can
   reach the mount targets): mount the file system, then
   `python scripts/migrate_efs.py --source <dataset> --mount <mount-point> --out evidence/efs-migration-<date>.json`.
3. Destroy the file system the same day (`terraform destroy` with
   `enable_efs=true`, after unmounting). Cost is from Cost
   Explorer, not a model.
