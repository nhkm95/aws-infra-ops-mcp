variable "instance_name" {
  description = "Instance name embedded in log-group paths."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+$", var.instance_name))
    error_message = "instance_name contains characters unsuitable for a log-group path."
  }
}

variable "retention_days" {
  description = "CloudWatch Logs retention period."
  type        = number
  default     = 7

  validation {
    condition = contains([
      1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731,
      1096, 1827, 2192, 2557, 2922, 3288, 3653,
    ], var.retention_days)
    error_message = "retention_days must be a CloudWatch Logs supported retention value."
  }
}
