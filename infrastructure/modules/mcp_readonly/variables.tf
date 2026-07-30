variable "name" {
  description = "Name of the customer-managed IAM policy."
  type        = string

  validation {
    condition     = length(var.name) >= 1 && length(var.name) <= 128
    error_message = "IAM policy name must contain between 1 and 128 characters."
  }
}

variable "log_group_arns" {
  description = "The two lab CloudWatch log-group ARNs queryable by the MCP."
  type        = list(string)

  validation {
    condition = (
      length(var.log_group_arns) == 2 &&
      alltrue([for arn in var.log_group_arns : startswith(arn, "arn:")])
    )
    error_message = "log_group_arns must contain exactly two AWS log-group ARNs."
  }
}

variable "instance_arn" {
  description = "Exact ARN of the single EC2 instance targetable by the MCP."
  type        = string

  validation {
    condition     = startswith(var.instance_arn, "arn:") && strcontains(var.instance_arn, ":ec2:")
    error_message = "instance_arn must be an EC2 instance ARN."
  }
}

variable "runtime_role_name" {
  description = "Name of the IAM role used by the local MCP server."
  type        = string

  validation {
    condition     = length(var.runtime_role_name) >= 1 && length(var.runtime_role_name) <= 64
    error_message = "IAM role name must contain between 1 and 64 characters."
  }
}

variable "trusted_sso_role_arn_pattern" {
  description = "IAM Identity Center AWSReservedSSO role ARN pattern allowed to assume the runtime role."
  type        = string

  validation {
    condition = can(regex(
      "^arn:aws:iam::[0-9]{12}:role/aws-reserved/sso\\.amazonaws\\.com/[a-z0-9-]+/AWSReservedSSO_[A-Za-z0-9+=,.@_-]+_\\*$",
      var.trusted_sso_role_arn_pattern
    ))
    error_message = "trusted_sso_role_arn_pattern must be an IAM Identity Center AWSReservedSSO role ARN pattern ending in an asterisk."
  }
}
