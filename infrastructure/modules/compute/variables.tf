variable "name" {
  description = "Name prefix for compute IAM resources."
  type        = string

  validation {
    condition     = length(trimspace(var.name)) > 0
    error_message = "name must not be empty."
  }
}

variable "instance_name" {
  description = "EC2 Name tag and MCP allowlisted identity."
  type        = string

  validation {
    condition     = var.instance_name == "web01"
    error_message = "This compute module is intentionally restricted to web01."
  }
}

variable "instance_type" {
  description = "EC2 instance type."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must resemble a valid EC2 instance type."
  }
}

variable "root_volume_size" {
  description = "Encrypted gp3 root volume size in GiB."
  type        = number

  validation {
    condition     = var.root_volume_size >= 8 && var.root_volume_size <= 16384
    error_message = "root_volume_size must be between 8 and 16384 GiB."
  }
}

variable "subnet_id" {
  description = "Subnet in which to launch the instance."
  type        = string
}

variable "security_group_id" {
  description = "Security group attached to the instance."
  type        = string
}

variable "system_log_group_name" {
  description = "CloudWatch log group for /var/log/messages."
  type        = string
}

variable "nginx_log_group_name" {
  description = "CloudWatch log group for nginx errors."
  type        = string
}
