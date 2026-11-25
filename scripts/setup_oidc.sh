#!/usr/bin/env bash
# Create (or update) the GitHub OIDC role for the nas-to-aws-migration
# repo, so the real-aws workflow can apply Terraform, run the drills,
# and destroy the stack without a long-lived access key.
#
# Idempotent: safe to re-run. It creates the OIDC identity provider
# only if it does not exist, creates the role only if it does not
# exist (otherwise it updates the trust policy and the session
# duration), and replaces the inline policy on every run.
#
# Usage (uses your own AWS CLI credentials; creates no credentials
# and stores none):
#   bash scripts/setup_oidc.sh [AWS_ACCOUNT_ID]
#   AWS_ACCOUNT_ID=123456789012 bash scripts/setup_oidc.sh
# If no account ID is given, it reads the account from
# `aws sts get-caller-identity` (your configured AWS CLI profile).
#
# After it runs, set the repo variable AWS_OIDC_ROLE_ARN to the
# printed role ARN (Settings > Secrets and variables > Actions >
# Variables). Never put the role ARN in a file.
set -euo pipefail

REPO="chanawh/nas-to-aws-migration"
ROLE_NAME="nas2aws-migration-github-oidc"
POLICY_NAME="nas2aws-migration-github-oidc"
REGION="${AWS_REGION:-us-west-2}"
OIDC_URL="https://token.actions.githubusercontent.com"
# Thumbprint for token.actions.githubusercontent.com (GitHub's OIDC
# provider). If AWS rejects it, create the provider without
# --thumbprint-list and AWS fetches the current one.
OIDC_THUMBPRINT="6938fd4d98bab03faadb97b34396831e3780aea1"
# 12 hours: the workflow asks for a 4-hour session
# (role-duration-seconds: 14400), which must fit inside this.
MAX_SESSION_SECONDS=43200

ACCOUNT_ID="${1:-${AWS_ACCOUNT_ID:-}}"
if [ -z "$ACCOUNT_ID" ]; then
  echo "No account ID given; reading it from the configured AWS CLI profile..."
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi
if ! [[ "$ACCOUNT_ID" =~ ^[0-9]{12}$ ]]; then
  echo "error: account ID must be 12 digits, got '$ACCOUNT_ID'" >&2
  echo "usage: bash scripts/setup_oidc.sh [AWS_ACCOUNT_ID]" >&2
  exit 1
fi
echo "account: $ACCOUNT_ID  region: $REGION"

PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# --- OIDC identity provider (create only if missing) ---
if aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  echo "OIDC provider already exists: $PROVIDER_ARN"
else
  echo "Creating OIDC identity provider for $OIDC_URL ..."
  aws iam create-open-id-connect-provider \
    --url "$OIDC_URL" \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list "$OIDC_THUMBPRINT" >/dev/null
  echo "OIDC provider created."
fi

# --- Trust policy: only this repo's Actions can assume the role. ---
TRUST_POLICY="$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "${PROVIDER_ARN}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:*"
        }
      }
    }
  ]
}
EOF
)"

# --- Role (create or update) ---
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role $ROLE_NAME exists; updating trust policy and session duration..."
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" \
    --policy-document "$TRUST_POLICY" >/dev/null
  aws iam update-role --role-name "$ROLE_NAME" \
    --max-session-duration "$MAX_SESSION_SECONDS" >/dev/null
else
  echo "Creating role $ROLE_NAME ..."
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_POLICY" \
    --max-session-duration "$MAX_SESSION_SECONDS" \
    --description "GitHub OIDC role for the nas-to-aws-migration real-aws workflow" \
    --tags Key=project,Value=nas-to-aws-migration >/dev/null
fi

# --- Permissions policy (inline; replaced on every run) ---
# Terraform apply/destroy for the stack (S3, KMS, DynamoDB, SNS, SQS,
# Lambda, IAM, CloudWatch) plus the drill permissions (the four share
# buckets, the KMS drill's own keys, Object Lock bypass for cleanup)
# plus the FSx / DataSync / CloudTrail / VPC permissions the workflow
# needs when enable_fsx is true. Resource ARNs are scoped to the
# project's names where the service allows it; actions that cannot be
# scoped (CreateKey, list/describe calls) use "*".
POLICY="$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3ProjectBuckets",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket", "s3:DeleteBucket",
        "s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketVersions",
        "s3:GetBucketVersioning", "s3:PutBucketVersioning",
        "s3:GetBucketTagging", "s3:PutBucketTagging",
        "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
        "s3:GetBucketEncryption", "s3:PutBucketEncryption",
        "s3:GetBucketLifecycleConfiguration", "s3:PutBucketLifecycleConfiguration",
        "s3:GetBucketNotification", "s3:PutBucketNotification",
        "s3:GetBucketObjectLockConfiguration", "s3:PutObjectLockConfiguration",
        "s3:GetBucketPolicy", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
        "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
        "s3:GetObjectVersion", "s3:DeleteObjectVersion",
        "s3:GetObjectTagging", "s3:PutObjectTagging", "s3:DeleteObjectTagging",
        "s3:GetObjectVersionTagging", "s3:PutObjectVersionTagging",
        "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts",
        "s3:BypassGovernanceRetention"
      ],
      "Resource": [
        "arn:aws:s3:::nas2aws-migration-*",
        "arn:aws:s3:::nas2aws-migration-*/*"
      ]
    },
    {
      "Sid": "KMSProjectKeys",
      "Effect": "Allow",
      "Action": [
        "kms:DescribeKey", "kms:GetKeyPolicy", "kms:PutKeyPolicy",
        "kms:EnableKeyRotation", "kms:GetKeyRotationStatus",
        "kms:DisableKey", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion",
        "kms:CreateAlias", "kms:DeleteAlias", "kms:UpdateAlias",
        "kms:TagResource", "kms:UntagResource",
        "kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"
      ],
      "Resource": [
        "arn:aws:kms:${REGION}:${ACCOUNT_ID}:key/*",
        "arn:aws:kms:${REGION}:${ACCOUNT_ID}:alias/nas2aws-migration"
      ]
    },
    {
      "Sid": "DynamoDBAuditTable",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable", "dynamodb:DeleteTable", "dynamodb:DescribeTable",
        "dynamodb:UpdateTable", "dynamodb:TagResource", "dynamodb:UntagResource",
        "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:DeleteItem",
        "dynamodb:Scan", "dynamodb:Query", "dynamodb:DescribeTimeToLive"
      ],
      "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/nas2aws-migration-audit"
    },
    {
      "Sid": "LambdaValidator",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction",
        "lambda:GetFunctionConfiguration", "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration", "lambda:TagResource",
        "lambda:UntagResource", "lambda:AddPermission", "lambda:RemovePermission",
        "lambda:CreateEventSourceMapping", "lambda:DeleteEventSourceMapping",
        "lambda:GetEventSourceMapping", "lambda:ListEventSourceMappings",
        "lambda:InvokeFunction"
      ],
      "Resource": "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:nas2aws-validator"
    },
    {
      "Sid": "SNSAlerts",
      "Effect": "Allow",
      "Action": [
        "sns:CreateTopic", "sns:DeleteTopic", "sns:GetTopicAttributes",
        "sns:SetTopicAttributes", "sns:Subscribe", "sns:Unsubscribe",
        "sns:Publish", "sns:TagResource", "sns:UntagResource"
      ],
      "Resource": "arn:aws:sns:${REGION}:${ACCOUNT_ID}:nas2aws-migration-alerts"
    },
    {
      "Sid": "SQSQueues",
      "Effect": "Allow",
      "Action": [
        "sqs:CreateQueue", "sqs:DeleteQueue", "sqs:GetQueueAttributes",
        "sqs:SetQueueAttributes", "sqs:GetQueueUrl", "sqs:SendMessage",
        "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:TagQueue",
        "sqs:UntagQueue", "sqs:SetQueuePolicy", "sqs:GetQueuePolicy"
      ],
      "Resource": "arn:aws:sqs:${REGION}:${ACCOUNT_ID}:nas2aws-migration-*"
    },
    {
      "Sid": "IAMProjectIdentities",
      "Effect": "Allow",
      "Action": [
        "iam:CreateUser", "iam:DeleteUser", "iam:GetUser", "iam:TagUser",
        "iam:UntagUser", "iam:PutUserPolicy", "iam:DeleteUserPolicy",
        "iam:GetUserPolicy", "iam:AttachUserPolicy", "iam:DetachUserPolicy",
        "iam:ListAttachedUserPolicies",
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole",
        "iam:UntagRole", "iam:PutRolePolicy", "iam:DeleteRolePolicy",
        "iam:GetRolePolicy", "iam:ListRolePolicies",
        "iam:UpdateAssumeRolePolicy", "iam:PassRole",
        "iam:CreatePolicy", "iam:DeletePolicy", "iam:GetPolicy",
        "iam:GetPolicyVersion", "iam:ListPolicyVersions",
        "iam:CreatePolicyVersion", "iam:DeletePolicyVersion"
      ],
      "Resource": [
        "arn:aws:iam::${ACCOUNT_ID}:user/nas2aws-migration",
        "arn:aws:iam::${ACCOUNT_ID}:role/nas2aws-validator",
        "arn:aws:iam::${ACCOUNT_ID}:role/nas2aws-datasync-s3",
        "arn:aws:iam::${ACCOUNT_ID}:policy/nas2aws-migration-s3"
      ]
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:DescribeLogGroups",
        "logs:TagLogGroup", "logs:UntagLogGroup", "logs:PutRetentionPolicy",
        "logs:DeleteRetentionPolicy"
      ],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/nas2aws-validator:*"
    },
    {
      "Sid": "CloudWatchAlarms",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms",
        "cloudwatch:DescribeAlarms", "cloudwatch:TagResource",
        "cloudwatch:UntagResource"
      ],
      "Resource": "arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:nas2aws-*"
    },
    {
      "Sid": "FSx",
      "Effect": "Allow",
      "Action": [
        "fsx:CreateFileSystem", "fsx:DeleteFileSystem", "fsx:DescribeFileSystems",
        "fsx:CreateStorageVirtualMachine", "fsx:DeleteStorageVirtualMachine",
        "fsx:DescribeStorageVirtualMachines",
        "fsx:CreateVolume", "fsx:DeleteVolume", "fsx:DescribeVolumes",
        "fsx:TagResource", "fsx:UntagResource", "fsx:DescribeBackups"
      ],
      "Resource": "arn:aws:fsx:${REGION}:${ACCOUNT_ID}:*"
    },
    {
      "Sid": "DataSync",
      "Effect": "Allow",
      "Action": [
        "datasync:CreateLocationS3", "datasync:CreateLocationFsxOntap",
        "datasync:DeleteLocation", "datasync:DescribeLocation",
        "datasync:CreateTask", "datasync:DeleteTask", "datasync:DescribeTask",
        "datasync:TagResource", "datasync:UntagResource"
      ],
      "Resource": [
        "arn:aws:datasync:${REGION}:${ACCOUNT_ID}:task/*",
        "arn:aws:datasync:${REGION}:${ACCOUNT_ID}:location/*",
        "arn:aws:datasync:${REGION}:${ACCOUNT_ID}:agent/*"
      ]
    },
    {
      "Sid": "CloudTrail",
      "Effect": "Allow",
      "Action": [
        "cloudtrail:CreateTrail", "cloudtrail:DeleteTrail", "cloudtrail:GetTrail",
        "cloudtrail:StartLogging", "cloudtrail:StopLogging",
        "cloudtrail:UpdateTrail", "cloudtrail:AddTags", "cloudtrail:RemoveTags",
        "cloudtrail:GetTrailStatus"
      ],
      "Resource": "arn:aws:cloudtrail:${REGION}:${ACCOUNT_ID}:trail/nas2aws-migration"
    },
    {
      "Sid": "EC2ForFSxVPC",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:DescribeVpcs",
        "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:DescribeSubnets",
        "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
        "ec2:DescribeSecurityGroups",
        "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
        "ec2:CreateTags", "ec2:DeleteTags",
        "ec2:DescribeAvailabilityZones", "ec2:DescribeAccountAttributes"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AccountLevelListAndCreate",
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "kms:CreateKey", "kms:ListKeys", "kms:ListAliases",
        "dynamodb:ListTables",
        "sns:ListTopics",
        "sqs:ListQueues",
        "iam:ListRoles", "iam:ListUsers", "iam:ListPolicies",
        "lambda:ListFunctions",
        "cloudtrail:DescribeTrails", "cloudtrail:ListTrails",
        "datasync:ListTasks", "datasync:ListLocations",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)"

aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document "$POLICY" >/dev/null
echo "Inline policy $POLICY_NAME attached to $ROLE_NAME."

echo ""
echo "Done. Role ARN:"
echo "  $ROLE_ARN"
echo ""
echo "Set the repo variable AWS_OIDC_ROLE_ARN to that ARN:"
echo "  GitHub repo > Settings > Secrets and variables > Actions > Variables"
echo "  > New repository variable > Name: AWS_OIDC_ROLE_ARN"
echo ""
echo "Then dispatch the workflow from the Actions tab:"
echo "  Actions > real-aws > Run workflow"
echo "  (per_share 2500 = 10 GB default; 12500 = 50 GB; enable_fsx for the"
echo "  FSx/DataSync version, ~\$4.20/day)"
