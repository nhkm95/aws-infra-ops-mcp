resource "aws_cloudwatch_log_group" "system" {
  name              = "/aws/mcp-lab/${var.instance_name}/system"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_log_group" "nginx" {
  name              = "/aws/mcp-lab/${var.instance_name}/nginx"
  retention_in_days = var.retention_days
}
