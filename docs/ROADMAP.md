# Final roadmap: from lab project to cloud-role candidate

This roadmap is the sequence that makes this project, and
its sibling PowerScale CSI project, count in a cloud-role
application. It is written for Andrew Chan, Technical
Support Engineer II at Dell Technologies since December
2018, escalation point for 150+ enterprise Dell PowerScale
(OneFS) environments and 1,400+ high-severity incidents.
Certifications: AWS Solutions Architect Professional, CKA,
Terraform Associate, RHCE, CCNA.

## Phase 1, publish the final form

1. Push both repos and the `v1.0.0` tags to GitHub.
2. Set repo description and topics. This repo:
   description "PowerScale NAS to AWS migration factory,
   Terraform + Python, S3, KMS, Lambda, DynamoDB, FSx,
   DataSync, verified on moto", topics `aws`,
   `terraform`, `python`, `migration`, `s3`.
3. Confirm CI green on the final commits.
4. Publish the GitHub profile README at `chanawh/chanawh`,
   linking this project, the CSI project, the PowerScale
   Prometheus exporter, and powerscale-async.

## Phase 2, prove it live

5. Run the CSI project on a real machine: OneFS Simulator
   in VMware Workstation Pro, kind, Dell's driver, four drills.
6. Run this project against LocalStack in Docker, then
   against the OneFS node with `--source-mode onefs`.
7. One-day real AWS run, `docs/REAL_AWS_ONE_DAY.md`,
   under $0.10 without FSx, about $4.20 with FSx, then destroy.
   The resume bullet counts only after a clean verify there.
8. Save evidence from those runs. Evidence beats claims.

## Phase 3, use it to get interviews

9. STAR bullet from `docs/INTERVIEW_GUIDE.md` on the resume,
   labelled lab/emulator until Phase 2 step 7 is done.
10. Rehearse the 30-second pitch and the ten Q&As out loud, timed.
11. Apply to Cloud Support Engineer, Cloud Operations,
    Storage and Platform Engineer first, SRE second.
    No mass-applying, no third project.
12. AZ-104 in parallel, the cert gate for Azure-titled roles.

## What not to do

Do not build more projects. Do not call an emulator
production. Do not claim 80 TB moved. The dataset is
24 files, the design is for 80 TB, and that distinction
is stated everywhere it matters.
