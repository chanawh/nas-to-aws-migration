# Interview guide: how to talk about this project

## The STAR bullet (use this on the resume, verbatim)

> Built a Terraform-provisioned NAS-to-AWS migration factory in a
> local AWS emulator (LocalStack / moto, $0): migrated a simulated
> 80 TB, 400-user PowerScale NAS to S3 with a Python sync engine
> using SHA-256 metadata verification, per-share quotas, and
> ACL-to-tag mapping; KMS-encrypted buckets, a Lambda validator
> re-checking every object into a DynamoDB audit table, SQS/SNS
> alerting and CloudWatch alarms; 24-file lab dataset verified
> 100% checksum-clean, cutover runbook and failure drills documented.

Do not change "simulated" to "migrated 80 TB". The dataset is 24
files, 1,163,264 bytes. The 80 TB is the design target, not the
data moved. Saying otherwise is the fastest way to lose the role
in a background check or a technical interview.

## The 30-second pitch

"I support PowerScale for a living, so I built the migration I
keep getting asked about: take an on-prem NAS off, put it in AWS,
prove nothing was corrupted, and cut over without downtime. The
sync hashes every file, a Lambda re-hashes it a second time, the
audit is in DynamoDB, and a failure pages through SNS. It runs
for free in an emulator, and the Terraform is the same code I
would run on real AWS, with FSx and DataSync gated for that run."

## Architecture, in one breath

OneFS SMB -> Python sync -> S3 with KMS -> S3 event -> SQS ->
Lambda validator -> DynamoDB audit, with SNS on failure and
CloudWatch watching the validator. On real AWS, DataSync then
copies S3 to FSx for NetApp ONTAP, the file system that serves
SMB/NFS to applications that cannot use objects.

## Ten questions you will be asked, and the answer

1. Why S3 and not EFS or FSx directly?
   S3 is the durable landing zone: cheap, versioned, and the
   checksum lives with the object. FSx is the working file
   system, and it costs ~$125/month minimum, so it is the last
   step, not the first. EFS is paid-only in the emulator and
   FSx is not emulated at all, which is why they are gated.

2. Why not trust the S3 ETag as a checksum?
   ETag is not a SHA-256, and for multipart uploads it is not
   even a hash of the file. The sync stores a real SHA-256 in
   Metadata and Tags, and verify re-hashes the actual bytes.

3. What happens if the sync dies halfway?
   The manifest in S3 is the resume point. Re-running sync
   re-hashes the target and only copies what is new or changed.
   The demo proves this: corrupt one object, verify fails on
   exactly that object, re-sync copies 1 file, 23 unchanged.

4. What happens if the Lambda validator dies?
   The message stays in SQS, retries 3 times, then goes to the
   dead-letter queue. CloudWatch alarms on Lambda errors and
   on a message older than 5 minutes in the queue.

5. How do you know the file was not corrupted in transit?
   Two independent hashes: the sync writes the hash, the
   Lambda re-downloads and re-hashes. A mismatch is written
   to DynamoDB as validated-mismatch and published to SNS.

6. How do quotas work?
   Checked before any copy in a share, from quotas.json.
   An over-quota share is skipped whole, not partially copied.

7. How do ACLs survive the move?
   acl.json is validated first (owner exists in users.csv,
   group is a real Department or MemberOf group, mode is
   rwx/r-x/r--). The AWS group per share is recorded in the
   same file. DataSync preserves POSIX permissions, UID and
   GID, both, on the real-AWS step.

8. Why is DataSync turned off in the emulator?
   The emulator has no code for FSx, so there is no file
   system for DataSync to deliver to. Turning it on would
   fail at the FSx step and prove nothing. It is validated
   in Terraform only, and enabled on real AWS.

9. What would break at 80 TB?
   The sync is single-threaded and re-hashes every target
   object, so it would be slow, not wrong. At real scale
   you parallelise per share, use S3 multipart upload, and
   let DataSync do the heavy copy. The 24-file dataset is
   a scale model, not a scale test, and you should say so.

10. What would you do differently on real AWS?
    Run it once for a day and destroy it (see
    docs/REAL_AWS_ONE_DAY.md, under $1 without FSx), enable
    CloudTrail, set alert_email, and use real IAM roles
    instead of a long-lived user. Then the resume bullet
    can drop the word "emulator".

## What not to say

- Do not say you migrated 80 TB, or that this ran in production.
- Do not say LocalStack ran live here. It did not. Moto did.
  LocalStack is the Docker path for your machine.
- Do not say the Lambda ran live in the emulator. It was
  created in moto; its handler was tested directly.
- Do not hide the emulator. Lead with it: "built for $0 in
  an emulator, Terraform is the same code as real AWS."
  Hiring managers trust that more than a vague "AWS project".
