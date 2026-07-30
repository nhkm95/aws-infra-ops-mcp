output "policy_arn" {
  description = "ARN of the unattached customer-managed MCP read-only policy."
  value       = aws_iam_policy.this.arn
}
