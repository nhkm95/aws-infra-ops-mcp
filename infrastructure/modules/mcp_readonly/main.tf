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
}

resource "aws_iam_policy" "this" {
  name        = var.name
  description = "Read-only EC2 health lookup for the AWS Infrastructure Operations MCP"
  policy      = data.aws_iam_policy_document.this.json
}
