variable "name" {
  description = "Name of the customer-managed IAM policy."
  type        = string

  validation {
    condition     = length(var.name) >= 1 && length(var.name) <= 128
    error_message = "IAM policy name must contain between 1 and 128 characters."
  }
}
