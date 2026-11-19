# FSx for NetApp ONTAP + DataSync: the file-share target.
# REAL AWS ONLY, gated by enable_fsx_datasync = false by default.
# Neither service is emulated for free: FSx is not emulated at all,
# and DataSync is not in LocalStack's free tiers or in moto. Enabling
# this against an emulator will fail at apply time, by design.
# When enabled on real AWS, the flow is:
#   OneFS SMB -> Python sync -> S3 -> DataSync task -> FSx ONTAP volume
# S3 is the durable object landing zone; FSx is the file system that
# serves SMB/NFS to the applications that cannot use objects.
# Cost on real AWS: FSx ONTAP minimum 1024 GiB, ~$0.12/GiB/month
# SSD, so ~$125/month minimum, plus throughput. Lab only: enable it,
# demo it, destroy it the same day.
resource "aws_vpc" "fsx" {
  count      = var.enable_fsx_datasync ? 1 : 0
  cidr_block = var.vpc_cidr
  tags       = merge(local.tags, { Name = "nas2aws-fsx" })
}

resource "aws_subnet" "fsx" {
  count             = var.enable_fsx_datasync ? 1 : 0
  vpc_id            = aws_vpc.fsx[0].id
  cidr_block        = var.fsx_subnet_cidr
  availability_zone = "${var.region}a"
  tags              = merge(local.tags, { Name = "nas2aws-fsx" })
}

resource "aws_security_group" "fsx" {
  count       = var.enable_fsx_datasync ? 1 : 0
  name        = "nas2aws-fsx"
  description = "FSx for NetApp ONTAP: NFS and SMB from the VPC only"
  vpc_id      = aws_vpc.fsx[0].id

  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "NFS"
  }

  ingress {
    from_port   = 445
    to_port     = 445
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "SMB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

resource "aws_fsx_ontap_file_system" "migration" {
  count               = var.enable_fsx_datasync ? 1 : 0
  storage_capacity    = 1024
  subnet_ids          = [aws_subnet.fsx[0].id]
  preferred_subnet_id = aws_subnet.fsx[0].id
  deployment_type     = "SINGLE_AZ_1"
  throughput_capacity = 128
  security_group_ids  = [aws_security_group.fsx[0].id]
  tags                = local.tags
}

resource "aws_fsx_ontap_storage_virtual_machine" "migration" {
  count          = var.enable_fsx_datasync ? 1 : 0
  file_system_id = aws_fsx_ontap_file_system.migration[0].id
  name           = "nas2aws-svm"
}

resource "aws_fsx_ontap_volume" "migration" {
  count                      = var.enable_fsx_datasync ? 1 : 0
  name                       = "nas2aws-vol"
  junction_path              = "/nas2aws"
  size_in_megabytes          = 102400
  storage_efficiency_enabled = true
  storage_virtual_machine_id = aws_fsx_ontap_storage_virtual_machine.migration[0].id
}

resource "aws_iam_role" "datasync_s3" {
  count = var.enable_fsx_datasync ? 1 : 0
  name  = "nas2aws-datasync-s3"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "datasync.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy" "datasync_s3" {
  count = var.enable_fsx_datasync ? 1 : 0
  name  = "nas2aws-datasync-s3"
  role  = aws_iam_role.datasync_s3[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:GetObject", "s3:PutObject"]
      Resource = [aws_s3_bucket.shares["finance"].arn, "${aws_s3_bucket.shares["finance"].arn}/*"]
    }]
  })
}

resource "aws_datasync_location_s3" "migration" {
  count         = var.enable_fsx_datasync ? 1 : 0
  s3_bucket_arn = aws_s3_bucket.shares["finance"].arn
  subdirectory  = "/"

  s3_config {
    bucket_access_role_arn = aws_iam_role.datasync_s3[0].arn
  }
}

resource "aws_datasync_location_fsx_ontap_file_system" "migration" {
  count = var.enable_fsx_datasync ? 1 : 0
  # fsx_filesystem_arn is computed by the provider from the SVM ARN,
  # it is not a settable argument in AWS provider 5.x.
  security_group_arns         = [aws_security_group.fsx[0].arn]
  storage_virtual_machine_arn = aws_fsx_ontap_storage_virtual_machine.migration[0].arn
  subdirectory                = "/nas2aws"
  protocol {
    nfs {
      mount_options {
        version = "NFS3"
      }
    }
  }
}

resource "aws_datasync_task" "migration" {
  count                    = var.enable_fsx_datasync ? 1 : 0
  name                     = "nas2aws-s3-to-fsx"
  source_location_arn      = aws_datasync_location_s3.migration[0].arn
  destination_location_arn = aws_datasync_location_fsx_ontap_file_system.migration[0].arn

  options {
    verify_mode            = "POINT_IN_TIME_CONSISTENT"
    posix_permissions      = "PRESERVE"
    uid                    = "BOTH"
    gid                    = "BOTH"
    preserve_deleted_files = "PRESERVE"
    transfer_mode          = "CHANGED"
  }

  tags = local.tags
}
