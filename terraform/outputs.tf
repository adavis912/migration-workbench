output "region" {
  value = var.region
}

output "knowledge_base_id" {
  value = aws_bedrockagent_knowledge_base.inventory.id
}

output "data_source_id" {
  value = aws_bedrockagent_data_source.inventory.data_source_id
}

output "source_bucket" {
  value = aws_s3_bucket.source.bucket
}

output "dynamodb_table" {
  value = aws_dynamodb_table.inventory.name
}

output "agent_read_policy_arn" {
  description = "Attach to whatever role runs the agents' inventory tools."
  value       = aws_iam_policy.agent_read.arn
}
