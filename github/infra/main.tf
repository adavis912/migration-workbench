# GitHub edition of the Migration Workbench: everything it needs in AWS, and nothing shared with the
# GitLab edition except read access to the Claude and Eraser API keys.
#   - GitHub Actions OIDC provider + a read-only plan role trusted only by adavis912/migration-pilots (main)
#   - Terraform state bucket for those plans (S3 lockfile, no DynamoDB)
#   - A second EC2 workbench (HTTPS only, Session Manager, no SSH) that installs itself from the
#     github-edition branch, and a nightly stop schedule
# Run from CloudShell with local state:  terraform init && terraform apply
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags {
    tags = { project = "capstone-migration", component = "github-edition", managed_by = "terraform" }
  }
}

variable "github_repo" { default = "adavis912/migration-pilots" }
variable "create_oidc_provider" {
  description = "false if the account already has the token.actions.githubusercontent.com provider"
  default     = true
}
variable "instance_type" { default = "t3.large" }
variable "stop_cron" { default = "cron(0 2 * * ? *)" }

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
  bucket = "capstone-gh-tfstate-${local.acct}"
  oidc   = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${local.acct}:oidc-provider/token.actions.githubusercontent.com"
}

# ---------------------------------------------------------------- GitHub Actions OIDC
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      # GitHub now sends immutable owner and repo IDs in the subject; the older form is kept for compatibility.
      values   = ["repo:${var.github_repo}:ref:refs/heads/main", "repo:adavis912@133921026/migration-pilots@1468097768:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name                 = "capstone-gh-plan"
  description          = "Read-only Terraform plan role for GitHub Actions in ${var.github_repo} (main branch only)"
  assume_role_policy   = data.aws_iam_policy_document.plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "plan_ro" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_state" {
  name = "terraform-state"
  role = aws_iam_role.plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.state.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.state.arn}/*" }
    ]
  })
}

# ---------------------------------------------------------------- Terraform state for the GitHub plans
resource "aws_s3_bucket" "state" {
  bucket        = local.bucket
  force_destroy = true
}
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------- workbench host role
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
  name               = "capstone-gh-workbench"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.wb.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "wb" {
  statement {
    sid     = "ReadSecrets"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone-gh/*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/anthropic-api-key-*",
      "arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone/eraser-api-key-*",
    ]
  }
  statement {
    sid       = "CreateOwnLogin"
    actions   = ["secretsmanager:CreateSecret", "secretsmanager:TagResource"]
    resources = ["arn:aws:secretsmanager:us-east-1:${local.acct}:secret:capstone-gh/dashboard-login-*"]
  }
  statement {
    sid    = "NeverChangeIam"
    effect = "Deny"
    actions = ["iam:Create*", "iam:Delete*", "iam:Put*", "iam:Attach*", "iam:Detach*", "iam:Update*", "iam:Add*",
      "iam:Remove*", "iam:Tag*", "iam:Untag*", "iam:Set*", "iam:Upload*", "iam:Change*", "iam:Reset*", "iam:Enable*",
    "iam:Deactivate*", "iam:Resync*", "iam:PassRole"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "wb" {
  name   = "github-workbench-access"
  role   = aws_iam_role.wb.id
  policy = data.aws_iam_policy_document.wb.json
}

resource "aws_iam_instance_profile" "wb" {
  name = "capstone-gh-workbench"
  role = aws_iam_role.wb.name
}

# ---------------------------------------------------------------- network: HTTPS in, nothing else
resource "aws_security_group" "wb" {
  name        = "capstone-gh-workbench"
  description = "GitHub-edition workbench: HTTPS in only; HTTPS/HTTP out"
  vpc_id      = data.aws_vpc.default.id
}
resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Workbench (HTTPS, own login)"
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.wb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

# ---------------------------------------------------------------- the instance
resource "aws_instance" "wb" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.wb.id]
  iam_instance_profile        = aws_iam_instance_profile.wb.name
  associate_public_ip_address = true
  user_data                   = <<-EOT
    #!/bin/bash
    dnf install -y -q git
    git clone -q -b github-edition https://github.com/adavis912/migration-workbench.git /tmp/wb
    bash /tmp/wb/github/install_github_workbench.sh
  EOT
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }
  tags       = { Name = "capstone-gh-migration-workbench" }
  lifecycle { ignore_changes = [ami, user_data] }
  depends_on = [aws_iam_role_policy.wb]
}

# ---------------------------------------------------------------- nightly stop
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
  name               = "capstone-gh-workbench-autostop"
  assume_role_policy = data.aws_iam_policy_document.sched_trust.json
}
resource "aws_iam_role_policy" "sched" {
  name = "stop-gh-workbench"
  role = aws_iam_role.sched.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ec2:StopInstances", Resource = aws_instance.wb.arn }]
  })
}
resource "aws_scheduler_schedule" "stop" {
  name                         = "capstone-gh-workbench-nightly-stop"
  schedule_expression          = var.stop_cron
  schedule_expression_timezone = "America/New_York"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.sched.arn
    input    = jsonencode({ InstanceIds = [aws_instance.wb.id] })
  }
}

output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "state_bucket" { value = aws_s3_bucket.state.id }
output "instance_id" { value = aws_instance.wb.id }
output "workbench_url" { value = "https://${aws_instance.wb.public_ip}" }
