variable "region" {
  description = "AWS Region. Must support Amazon Bedrock Knowledge Bases and Amazon S3 Vectors."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names (lowercase letters, numbers, hyphens)."
  type        = string
  default     = "hvig-kb"
}

variable "budget_limit_usd" {
  description = "Monthly AWS budget for the whole account; alerts fire at 80% actual and 100% forecast."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string
}

variable "embedding_dimensions" {
  description = "Titan Text Embeddings V2 output size: 1024, 512 or 256. Must match the vector index."
  type        = number
  default     = 1024
}
