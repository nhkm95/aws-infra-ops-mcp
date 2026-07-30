output "vpc_id" {
  description = "ID of the lab VPC."
  value       = module.network.vpc_id
}

output "subnet_id" {
  description = "ID of the public subnet."
  value       = module.network.public_subnet_id
}

output "instance_id" {
  description = "ID of the web01 EC2 instance."
  value       = module.compute.instance_id
}

output "public_ip" {
  description = "Public IPv4 address of the EC2 instance."
  value       = module.compute.public_ip
}

output "private_ip" {
  description = "Private IPv4 address of the EC2 instance."
  value       = module.compute.private_ip
}

output "instance_profile_name" {
  description = "Name of the EC2 instance profile."
  value       = module.compute.instance_profile_name
}

output "cloudwatch_log_group_names" {
  description = "CloudWatch log groups receiving system and nginx logs."
  value = {
    system = module.observability.system_log_group_name
    nginx  = module.observability.nginx_log_group_name
  }
}

output "mcp_readonly_policy_arn" {
  description = "ARN of the unattached MCP EC2 read-only IAM policy."
  value       = module.mcp_readonly.policy_arn
}
