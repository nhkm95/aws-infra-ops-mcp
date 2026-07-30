variable "aws_region" {
  description = "AWS Region in which to create the lab."
  type        = string
  default     = "ap-southeast-1"

  validation {
    condition     = length(trimspace(var.aws_region)) > 0
    error_message = "aws_region must not be empty."
  }
}

variable "project_name" {
  description = "Project tag applied to all supported resources."
  type        = string
  default     = "aws-infra-ops-mcp"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.project_name))
    error_message = "project_name must use lowercase letters, numbers, and hyphens."
  }
}

variable "environment" {
  description = "Environment tag applied to all supported resources."
  type        = string
  default     = "lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]*$", var.environment))
    error_message = "environment must use lowercase letters, numbers, and hyphens."
  }
}

variable "vpc_cidr" {
  description = "IPv4 CIDR for the lab VPC."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "public_subnet_cidr" {
  description = "IPv4 CIDR for the single public subnet."
  type        = string
  default     = "10.20.1.0/24"

  validation {
    condition     = can(cidrnetmask(var.public_subnet_cidr))
    error_message = "public_subnet_cidr must be a valid IPv4 CIDR."
  }
}

variable "instance_name" {
  description = "Name and policy-allowlisted identity of the EC2 instance."
  type        = string
  default     = "web01"

  validation {
    condition     = var.instance_name == "web01"
    error_message = "This lab currently supports only the allowlisted instance name web01."
  }
}

variable "instance_type" {
  description = "EC2 instance type for the lab host."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must resemble a valid EC2 instance type, such as t3.micro."
  }
}

variable "root_volume_size" {
  description = "Size in GiB of the encrypted gp3 root volume."
  type        = number
  default     = 10

  validation {
    condition     = var.root_volume_size >= 8 && var.root_volume_size <= 16384
    error_message = "root_volume_size must be between 8 and 16384 GiB."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention period."
  type        = number
  default     = 7

  validation {
    condition = contains([
      1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731,
      1096, 1827, 2192, 2557, 2922, 3288, 3653,
    ], var.log_retention_days)
    error_message = "log_retention_days must be a CloudWatch Logs supported retention value."
  }
}
