"""Fixed Terraform scaffolding the Builder does NOT write: provider pin, backend, tags, input variables.

Keeping these as templates means every application module uses the same pinned provider, the same
remote-state pattern, mandatory tags, and the same network/KMS inputs - Claude only writes the
application-specific resources (main.tf, outputs.tf), the pipeline and the migration notes.
"""

AWS_PROVIDER_VERSION = "6.73.0"

VERSIONS_TF = f'''terraform {{
  required_version = ">= 1.6.0"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "= {AWS_PROVIDER_VERSION}"
    }}
  }}
  # Remote state: bucket/key/region/lock settings are passed by the pipeline (-backend-config).
  backend "s3" {{}}
}}
'''

PROVIDERS_TF = '''provider "aws" {
  region = var.region
  default_tags {
    tags = {
      app_id              = var.app_id
      wave                = var.wave
      environment         = var.environment
      data_classification = var.data_classification
      managed_by          = "terraform"
      migration           = "oci-to-aws"
      generated_by        = "builder-agent (Claude), reviewed by reviewer-agent"
    }
  }
}
'''

VARIABLES_TF = '''variable "region" {
  type    = string
  default = "us-east-1"
}

variable "app_id" {
  type = string
}

variable "wave" {
  type = string
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "data_classification" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  description = "Private application subnets (no internet gateway route)."
  type        = list(string)
}

variable "onprem_cidrs" {
  description = "OCI/on-premises ranges reachable over Direct Connect + Transit Gateway during the hybrid period."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for EBS, RDS, ElastiCache and Secrets Manager."
  type        = string
}

variable "artifact_bucket_name" {
  description = "S3 bucket holding the application build artifacts that the pipeline deploys through SSM."
  type        = string
}

variable "route53_zone_id" {
  description = "Private hosted zone that serves the application's DNS name."
  type        = string
}

variable "app_dns_name" {
  description = "DNS name clients use for the application (moved from the legacy platform to AWS by weighted routing)."
  type        = string
}

variable "legacy_endpoint" {
  description = "Hostname of the application on the legacy (OCI) platform; receives the traffic not yet shifted to AWS."
  type        = string
}

variable "aws_traffic_weight" {
  description = "Share of traffic (0-100) routed to AWS. The pipeline's cutover stage moves it 5 -> 25 -> 100; rollback sets 0."
  type        = number
  default     = 0
  validation {
    condition     = var.aws_traffic_weight >= 0 && var.aws_traffic_weight <= 100
    error_message = "aws_traffic_weight must be between 0 and 100."
  }
}

variable "imported_ami_ids" {
  description = "AMI IDs created by AWS Application Migration Service for servers with no supported public AMI, keyed by server_id."
  type        = map(string)
  default     = {}
}

variable "acm_certificate_arn" {
  description = "ACM certificate for the application's DNS name; the load balancer terminates TLS with it."
  type        = string
}

variable "alb_log_bucket_name" {
  description = "Platform S3 bucket that receives load balancer access logs (its bucket policy already allows ELB log delivery)."
  type        = string
}
'''

TFVARS_EXAMPLE = '''# Example values - the pipeline supplies real ones per environment.
region              = "us-east-1"
app_id              = "{app_id}"
wave                = "{wave}"
environment         = "staging"
data_classification = "{classification}"
vpc_id              = "vpc-0123456789abcdef0"
private_subnet_ids  = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
onprem_cidrs        = ["10.40.0.0/16"]
kms_key_arn         = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
artifact_bucket_name = "hvig-app-artifacts-example"
route53_zone_id      = "Z0123456789EXAMPLE"
app_dns_name         = "{app_id_lower}.apps.hvig.internal"
legacy_endpoint      = "{app_id_lower}.oci.hvig.internal"
aws_traffic_weight   = 0
acm_certificate_arn  = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
alb_log_bucket_name  = "hvig-elb-logs-example"
'''

FIXED_FILES = {"versions.tf": VERSIONS_TF, "providers.tf": PROVIDERS_TF, "variables.tf": VARIABLES_TF}
GENERATED_FILES = ("main.tf", "outputs.tf", ".gitlab-ci.yml", "MIGRATION_NOTES.md")
