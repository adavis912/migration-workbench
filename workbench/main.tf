# Migration workbench: one EC2 instance that runs the agents, Terraform and GitLab pushes.
# No inbound ports: you connect with Session Manager. Stops itself every night at 8pm Eastern.
terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "6.73.0" }
  }
  backend "s3" {}
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "capstone-migration", component = "workbench", managed_by = "terraform" }
  }
}

variable "code_bundle" {
  description = "Local path of the project tarball the instance unpacks at first boot."
  type        = string
}
variable "instance_type" { default = "t3.large" }
variable "stop_cron" {
  description = "Nightly auto-stop (EventBridge Scheduler, America/New_York)."
  default     = "cron(0 20 * * ? *)"
}

data "aws_caller_identity" "me" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  acct   = data.aws_caller_identity.me.account_id
  bucket = "capstone-workbench-${local.acct}"
}

# ---------------------------------------------------------------- code bucket
resource "aws_s3_bucket" "wb" {
  bucket        = local.bucket
  force_destroy = true
}
resource "aws_s3_bucket_versioning" "wb" {
  bucket = aws_s3_bucket.wb.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "wb" {
  bucket = aws_s3_bucket.wb.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_public_access_block" "wb" {
  bucket                  = aws_s3_bucket.wb.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_object" "code" {
  bucket = aws_s3_bucket.wb.id
  key    = "bootstrap/capstone_kb.tgz"
  source = var.code_bundle
  etag   = filemd5(var.code_bundle)
}

# ---------------------------------------------------------------- instance role
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "wb" {
  name               = "capstone-workbench"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.wb.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy_attachment" "readonly" {
  role       = aws_iam_role.wb.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
data "aws_iam_policy_document" "wb" {
  statement {
    sid     = "ProjectSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/anthropic-api-key-*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/gitlab-push-token-*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/gitlab-read-token-*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/eraser-api-key-*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/dashboard-*",
    ]
  }
  statement {
    sid       = "ManageDashboardLogin"
    actions   = ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue", "secretsmanager:DescribeSecret", "secretsmanager:TagResource"]
    resources = ["arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/dashboard-*"]
  }
  # Scoped self-management: the workbench may change its own security-group rules and its own
  # auto-stop schedule, and nothing else. It can never change IAM, so it cannot widen its own access.
  statement {
    sid = "OwnSecurityGroupRules"
    actions = ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress", "ec2:ModifySecurityGroupRules",
    "ec2:UpdateSecurityGroupRuleDescriptionsIngress"]
    resources = [aws_security_group.wb.arn, "arn:aws:ec2:us-east-1:${local.acct}:security-group-rule/*"]
  }
  statement {
    sid       = "OwnSchedule"
    actions   = ["scheduler:UpdateSchedule", "scheduler:GetSchedule"]
    resources = ["arn:aws:scheduler:us-east-1:${local.acct}:schedule/default/capstone-workbench-nightly-stop"]
  }
  statement {
    sid       = "PassOnlyTheAutoStopRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.acct}:role/capstone-workbench-autostop"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
  statement {
    sid    = "NeverChangeIam"
    effect = "Deny"
    actions = ["iam:Create*", "iam:Delete*", "iam:Put*", "iam:Attach*", "iam:Detach*", "iam:Update*", "iam:Add*",
      "iam:Remove*", "iam:Tag*", "iam:Untag*", "iam:Set*", "iam:Upload*", "iam:Change*", "iam:Reset*", "iam:Enable*",
    "iam:Deactivate*", "iam:Resync*"]
    resources = ["*"]
  }
}
resource "aws_iam_role_policy" "wb" {
  name   = "workbench-access"
  role   = aws_iam_role.wb.id
  policy = data.aws_iam_policy_document.wb.json
}
resource "aws_iam_instance_profile" "wb" {
  name = "capstone-workbench"
  role = aws_iam_role.wb.name
}

# ---------------------------------------------------------------- network: no inbound at all
resource "aws_security_group" "wb" {
  name        = "capstone-workbench"
  description = "Migration workbench: no inbound; HTTPS/HTTP out for packages, GitLab, Claude API and AWS APIs"
  vpc_id      = data.aws_vpc.default.id
}
resource "aws_vpc_security_group_ingress_rule" "dashboard" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Pipeline dashboard (HTTPS, own login)"
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "HTTPS out"
}
resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "HTTP out (package mirrors)"
}

# ---------------------------------------------------------------- instance
resource "aws_instance" "wb" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.wb.id]
  iam_instance_profile        = aws_iam_instance_profile.wb.name
  associate_public_ip_address = true # default VPC has no NAT; still no inbound rules
  user_data_replace_on_change = false
  user_data = templatefile("${path.module}/bootstrap.sh", {
    bucket = aws_s3_bucket.wb.id
    key    = aws_s3_object.code.key
  })

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 40
    encrypted   = true
  }
  tags = { Name = "capstone-migration-workbench" }

  lifecycle { ignore_changes = [ami, user_data] }
  depends_on = [aws_s3_object.code, aws_iam_role_policy.wb]
}

# ---------------------------------------------------------------- nightly auto-stop
data "aws_iam_policy_document" "sched_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "sched" {
  name               = "capstone-workbench-autostop"
  assume_role_policy = data.aws_iam_policy_document.sched_trust.json
}
resource "aws_iam_role_policy" "sched" {
  name = "stop-workbench"
  role = aws_iam_role.sched.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ec2:StopInstances", Resource = aws_instance.wb.arn }]
  })
}
resource "aws_scheduler_schedule" "stop" {
  name                         = "capstone-workbench-nightly-stop"
  schedule_expression          = var.stop_cron
  schedule_expression_timezone = "America/New_York"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.sched.arn
    input    = jsonencode({ InstanceIds = [aws_instance.wb.id] })
  }
}

output "instance_id" { value = aws_instance.wb.id }
output "bucket" { value = aws_s3_bucket.wb.id }
output "dashboard_url" { value = "https://${aws_instance.wb.public_ip}/ (changes after each stop/start)" }
output "connect" { value = "EC2 console > ${aws_instance.wb.id} > Connect > Session Manager" }
