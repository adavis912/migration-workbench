data "aws_caller_identity" "current" {}

locals {
  account_id          = data.aws_caller_identity.current.account_id
  source_bucket_name  = "${var.name_prefix}-source-${local.account_id}"
  vector_bucket_name  = "${var.name_prefix}-vectors-${local.account_id}"
  index_name          = "inventory"
  embedding_model_arn = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
}

# ---------------------------------------------------------------- source documents (chunks + metadata)
resource "aws_s3_bucket" "source" {
  bucket        = local.source_bucket_name
  force_destroy = true # course project: lets `terraform destroy` remove the uploaded synthetic files
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---------------------------------------------------------------- vector store: Amazon S3 Vectors
resource "aws_s3vectors_vector_bucket" "kb" {
  vector_bucket_name = local.vector_bucket_name
  force_destroy      = true
}

resource "aws_s3vectors_index" "kb" {
  index_name         = local.index_name
  vector_bucket_name = aws_s3vectors_vector_bucket.kb.vector_bucket_name
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = "cosine"

  # Required by Bedrock Knowledge Bases: chunk text and Bedrock's own metadata are stored
  # as non-filterable keys, which keeps them out of the 2 KB filterable-metadata limit.
  metadata_configuration {
    non_filterable_metadata_keys = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
  }
}

# ---------------------------------------------------------------- service role for the knowledge base
data "aws_iam_policy_document" "kb_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:bedrock:${var.region}:${local.account_id}:knowledge-base/*"]
    }
  }
}

resource "aws_iam_role" "kb" {
  name               = "${var.name_prefix}-kb-role"
  assume_role_policy = data.aws_iam_policy_document.kb_trust.json
}

data "aws_iam_policy_document" "kb_access" {
  statement {
    sid       = "EmbeddingModel"
    actions   = ["bedrock:InvokeModel"]
    resources = [local.embedding_model_arn]
  }
  statement {
    sid       = "ListSourceBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.source.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid       = "ReadSourceObjects"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.source.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid = "VectorIndex"
    actions = [
      "s3vectors:PutVectors",
      "s3vectors:GetVectors",
      "s3vectors:DeleteVectors",
      "s3vectors:QueryVectors",
      "s3vectors:GetIndex",
    ]
    resources = [aws_s3vectors_index.kb.index_arn]
  }
}

resource "aws_iam_role_policy" "kb_access" {
  name   = "${var.name_prefix}-kb-access"
  role   = aws_iam_role.kb.id
  policy = data.aws_iam_policy_document.kb_access.json
}

# New IAM roles take a few seconds to become usable by Bedrock.
resource "time_sleep" "iam_propagation" {
  create_duration = "20s"
  depends_on      = [aws_iam_role_policy.kb_access]
}

# ---------------------------------------------------------------- Bedrock knowledge base + data source
resource "aws_bedrockagent_knowledge_base" "inventory" {
  name        = "${var.name_prefix}-inventory"
  description = "Synthetic application inventory for the capstone migration agent"
  role_arn    = aws_iam_role.kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = local.embedding_model_arn
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions          = var.embedding_dimensions
          embedding_data_type = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.kb.index_arn
    }
  }

  depends_on = [time_sleep.iam_propagation]
}

resource "aws_bedrockagent_data_source" "inventory" {
  knowledge_base_id    = aws_bedrockagent_knowledge_base.inventory.id
  name                 = "${var.name_prefix}-chunks"
  data_deletion_policy = "DELETE"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn         = aws_s3_bucket.source.arn
      inclusion_prefixes = ["kb/"]
    }
  }

  # Each file is already one chunk (one app profile, dependency summary or owner note),
  # so Bedrock must not split it again.
  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "NONE"
    }
  }
}

# ---------------------------------------------------------------- exact lookups + dependency graph
# Single table. PK = APP#<app_id>; SK = PROFILE | SERVER#<id> | DB#<id> | DEP#<target>#<kind> | RDEP#<source>#<kind>
resource "aws_dynamodb_table" "inventory" {
  name         = "${var.name_prefix}-inventory"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }
}

# ---------------------------------------------------------------- read-only policy for the agents' inventory tools
data "aws_iam_policy_document" "agent_read" {
  statement {
    sid       = "RetrieveFromKnowledgeBase"
    actions   = ["bedrock:Retrieve"]
    resources = [aws_bedrockagent_knowledge_base.inventory.arn]
  }
  statement {
    sid       = "ReadInventoryTable"
    actions   = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.inventory.arn]
  }
}

resource "aws_iam_policy" "agent_read" {
  name        = "${var.name_prefix}-agent-read"
  description = "Read-only access for the agents' inventory MCP server (Retrieve + DynamoDB reads)"
  policy      = data.aws_iam_policy_document.agent_read.json
}

# ---------------------------------------------------------------- cost guardrail
resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
