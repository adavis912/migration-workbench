terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # S3 Vectors as a Bedrock Knowledge Base vector store needs provider 6.27.0 or newer.
      version = ">= 6.27.0, < 7.0.0"
    }
    time = {
      source  = "hashicorp/time"
      version = ">= 0.11.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "capstone-migration-agent"
      Data      = "synthetic"
      ManagedBy = "terraform"
    }
  }
}
