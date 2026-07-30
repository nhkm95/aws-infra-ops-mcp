output "instance_id" {
  description = "ID of the EC2 instance."
  value       = aws_instance.this.id
}

output "instance_arn" {
  description = "ARN of the EC2 instance."
  value       = aws_instance.this.arn
}

output "public_ip" {
  description = "Public IPv4 address assigned to the instance."
  value       = aws_instance.this.public_ip
}

output "private_ip" {
  description = "Private IPv4 address assigned to the instance."
  value       = aws_instance.this.private_ip
}

output "instance_profile_name" {
  description = "Name of the instance profile attached to EC2."
  value       = aws_iam_instance_profile.this.name
}
