locals {
  # CloudWatch Logs IAM policies require the log-group resource form ending in
  # :*. Normalize inputs so an already-suffixed ARN does not become :*:*.
  log_group_iam_arns = [
    for arn in var.log_group_arns : "${trimsuffix(arn, ":*")}:*"
  ]
}

data "aws_iam_policy_document" "this" {
  statement {
    sid    = "DescribeEc2InstanceHealth"
    effect = "Allow"

    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceStatus",
    ]

    # EC2 Describe APIs generally do not support resource-level IAM permissions,
    # so AWS requires Resource "*" even though this policy grants no write access.
    resources = ["*"]
  }

  statement {
    sid    = "StartApprovedLogsInsightsQueries"
    effect = "Allow"

    actions = [
      "logs:StartQuery",
    ]

    resources = local.log_group_iam_arns
  }

  statement {
    sid    = "ReadAndStopLogsInsightsQueries"
    effect = "Allow"

    actions = [
      "logs:GetQueryResults",
      "logs:StopQuery",
    ]

    # These query-level APIs do not support resource-level IAM permissions.
    resources = ["*"]
  }

  statement {
    sid    = "RunFixedNginxStatusDocumentOnLabInstance"
    effect = "Allow"

    actions = ["ssm:SendCommand"]
    resources = [
      aws_ssm_document.nginx_status.arn,
      var.instance_arn,
    ]
  }

  statement {
    sid       = "ReadApprovedEc2Metrics"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricData"]
    resources = ["*"]
  }

  statement {
    sid    = "ReadCommandInvocation"
    effect = "Allow"

    actions = ["ssm:GetCommandInvocation"]

    # GetCommandInvocation does not support resource-level permissions.
    resources = ["*"]
  }
}

resource "aws_ssm_document" "nginx_status" {
  name            = "mcp-lab-get-nginx-status"
  document_type   = "Command"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Return fixed read-only nginx service status evidence"
    parameters    = {}
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "getNginxStatus"
        precondition = {
          StringEquals = [
            "platformType",
            "Linux",
          ]
        }
        inputs = {
          timeoutSeconds = "10"
          runCommand = [
            "active_state=\"$(systemctl is-active nginx 2>/dev/null || true)\"",
            "sub_state=\"$(systemctl show nginx --property=SubState --value 2>/dev/null || true)\"",
            "enabled_state=\"$(systemctl is-enabled nginx 2>/dev/null || true)\"",
            "enabled_at_boot=false",
            "[ \"$enabled_state\" = \"enabled\" ] && enabled_at_boot=true || true",
            "printf '{\"service_name\":\"nginx\",\"active_state\":\"%s\",\"sub_state\":\"%s\",\"enabled_at_boot\":%s}\\n' \"$active_state\" \"$sub_state\" \"$enabled_at_boot\"",
            "exit 0",
          ]
        }
      }
    ]
  })
}

resource "aws_iam_policy" "this" {
  name        = var.name
  description = "Read-only EC2, CloudWatch metrics and Logs lookup for the Infrastructure Operations MCP"
  policy      = data.aws_iam_policy_document.this.json
}

data "aws_iam_policy_document" "runtime_assume_role" {
  statement {
    sid     = "AllowTrustedIdentityCenterRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::004401752458:root"]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = [var.trusted_sso_role_arn_pattern]
    }
  }
}

resource "aws_iam_role" "runtime" {
  name                 = var.runtime_role_name
  description          = "Least-privilege runtime role for the local AWS Infrastructure Operations MCP server"
  assume_role_policy   = data.aws_iam_policy_document.runtime_assume_role.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "runtime_diagnostics_readonly" {
  role       = aws_iam_role.runtime.name
  policy_arn = aws_iam_policy.this.arn
}
