# EFS: the secondary migration target, for workloads that need a
# POSIX file system and cannot go to object storage. S3 is the
# primary target; EFS is the file-system target for the same data,
# populated by scripts/migrate_efs.py (copy + SHA-256 verify).
# REAL AWS ONLY, gated by enable_efs = false by default. EFS is
# not emulated (no local EFS emulator; the interview guide notes
# EFS is paid-only in the emulator), so enabling this against an
# emulator will fail at apply time, by design.
#
# Network choice: mount targets live in the DEFAULT VPC, via data
# sources, not in a new VPC. EFS only needs subnets for mount
# targets; the default VPC has one per AZ, and creating a whole
# VPC just to mount EFS would add network resources that are not
# part of the migration story. The FSx file creates its own VPC
# because FSx ONTAP needs a dedicated network; EFS does not.
# Lab choice: a production design would use a dedicated VPC with
# the same mount-target pattern. If the default VPC has been
# deleted in the account, this apply fails; the fix is a dedicated
# VPC (same resources, vpc_id from that VPC).
data "aws_vpc" "default" {
  count   = var.enable_efs ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = var.enable_efs ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

locals {
  # Two mount targets, one per AZ, from the first two default
  # subnets. Two AZs is the EFS recommendation for availability;
  # more AZs is a larger lab, not a different design.
  efs_subnet_ids = var.enable_efs ? slice(
    data.aws_subnets.default[0].ids, 0,
    min(2, length(data.aws_subnets.default[0].ids)),
  ) : []
}

resource "aws_security_group" "efs" {
  count       = var.enable_efs ? 1 : 0
  name        = "nas2aws-efs"
  description = "EFS: NFS from the VPC only"
  vpc_id      = data.aws_vpc.default[0].id

  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default[0].cidr_block]
    description = "NFS"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

resource "aws_efs_file_system" "migration" {
  count            = var.enable_efs ? 1 : 0
  creation_token   = "nas2aws-migration"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = local.tags
}

resource "aws_efs_mount_target" "migration" {
  for_each        = toset(local.efs_subnet_ids)
  file_system_id  = aws_efs_file_system.migration[0].id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs[0].id]
}
