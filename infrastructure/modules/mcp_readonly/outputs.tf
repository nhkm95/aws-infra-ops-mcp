output "policy_arn" {
  description = "ARN of the customer-managed MCP diagnostics read-only policy."
  value       = aws_iam_policy.this.arn
}

output "ssm_document_name" {
  description = "Name of the fixed read-only nginx status SSM document."
  value       = aws_ssm_document.nginx_status.name
}

output "ssm_document_arn" {
  description = "ARN of the fixed read-only nginx status SSM document."
  value       = aws_ssm_document.nginx_status.arn
}

output "ssm_journal_document_name" {
  description = "Name of the fixed read-only nginx journal SSM document."
  value       = aws_ssm_document.nginx_journal.name
}

output "ssm_journal_document_arn" {
  description = "ARN of the fixed read-only nginx journal SSM document."
  value       = aws_ssm_document.nginx_journal.arn
}

output "runtime_role_name" {
  description = "Name of the least-privilege MCP runtime IAM role."
  value       = aws_iam_role.runtime.name
}

output "runtime_role_arn" {
  description = "ARN of the least-privilege MCP runtime IAM role."
  value       = aws_iam_role.runtime.arn
}
