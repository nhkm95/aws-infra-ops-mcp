output "system_log_group_name" {
  description = "Name of the system CloudWatch log group."
  value       = aws_cloudwatch_log_group.system.name
}

output "nginx_log_group_name" {
  description = "Name of the nginx CloudWatch log group."
  value       = aws_cloudwatch_log_group.nginx.name
}

output "system_log_group_arn" {
  description = "ARN of the system CloudWatch log group."
  value       = aws_cloudwatch_log_group.system.arn
}

output "nginx_log_group_arn" {
  description = "ARN of the nginx CloudWatch log group."
  value       = aws_cloudwatch_log_group.nginx.arn
}
