# Terraform AWS lab

This directory defines a small AWS lab for the Infrastructure Operations MCP.
It creates one Amazon Linux 2023 `web01` instance in a public subnet, sends
system and nginx error logs to CloudWatch Logs, enables Systems Manager, and
creates the customer-managed diagnostics policy attached to the dedicated
`aws-infra-ops-mcp-lab-runtime` role used by the local MCP server. This MCP
runtime role is separate from the EC2 instance role that lets `web01` register
with Systems Manager and publish logs.

No inbound network access is created. The instance security group has no
ingress rules, including no SSH (22) or HTTP (80). Administration and validation
use AWS Systems Manager rather than an SSH key.

## Architecture

```text
VPC 10.20.0.0/16
└── public subnet 10.20.1.0/24 (first available AZ)
    ├── route 0.0.0.0/0 -> internet gateway
    └── web01 (t3.micro, public IPv4, no inbound rules)
        ├── IMDSv2 required
        ├── 10-GiB encrypted gp3 root disk
        ├── HTTPS-only security-group egress
        ├── SSM managed-instance role
        └── CloudWatch Agent
            ├── /var/log/messages -> /aws/mcp-lab/web01/system
            └── /var/log/nginx/error.log -> /aws/mcp-lab/web01/nginx
```

The public IPv4 address provides inexpensive outbound connectivity without a
NAT gateway or VPC interface endpoints. It does not make the instance reachable
because the security group permits no inbound traffic.

The `mcp_readonly` module creates the customer-managed
`aws-infra-ops-mcp-lab-diagnostics-readonly` policy and attaches it only to the
dedicated `aws-infra-ops-mcp-lab-runtime` role. The trusted IAM Identity Center
permission-set role assumes that runtime role for sessions of no more than one
hour. The runtime role has no inline policy or instance profile.
Its CloudTrail access is limited to `cloudtrail:LookupEvents` with
`Resource = "*"`, which AWS requires because Event History lookup does not
support resource-level permissions. It has no CloudTrail write,
trail-management, Lake, S3, or organization permissions.

## Potential costs

Applying this configuration can incur charges for:

- the `t3.micro` EC2 instance;
- the 10-GiB gp3 EBS root volume;
- the public IPv4 address while allocated;
- CloudWatch Logs ingestion, storage, and retrieval.

Free-tier eligibility varies by account and date. There is no NAT gateway or
interface-endpoint hourly cost in this design, but normal data-transfer charges
can still apply. Review current AWS pricing for `ap-southeast-1` before applying.

## Prerequisites

- Terraform 1.6 or newer
- AWS credentials from the normal AWS credential-provider chain
- An AWS Region configured, defaulting here to `ap-southeast-1`
- Permissions to create the documented VPC, EC2, IAM, CloudWatch Logs, and
  related resources, and to read the public AL2023 SSM AMI parameter

Do not store AWS credentials in this repository. Copy the example variables
only when overrides are needed:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Local `.tfvars` files, Terraform state, plans, crash logs, and `.terraform/`
working directories are ignored by Git. State uses Terraform's local backend;
there is intentionally no remote backend yet.

## Initialize and validate

Run commands from this `infrastructure/` directory:

```bash
terraform init
terraform fmt -check -recursive
terraform validate
```

Preview the proposed resources without changing AWS:

```bash
terraform plan -out=lab.tfplan
```

Review the plan carefully. To create the lab explicitly:

```bash
terraform apply lab.tfplan
```

## Validate through Systems Manager

Wait until `web01` appears as a managed node in Systems Manager Fleet Manager.
Because there is no inbound access or SSH key, use Session Manager from the AWS
console or, with the Session Manager plugin installed:

```bash
aws ssm start-session --target "$(terraform output -raw instance_id)"
```

Inside the session, useful read-only checks include:

```bash
systemctl status nginx rsyslog amazon-cloudwatch-agent
curl --fail http://127.0.0.1/
```

Confirm that both CloudWatch log groups receive streams for the instance. The
instance profile includes `AmazonSSMManagedInstanceCore` and
`CloudWatchAgentServerPolicy`; no credentials are embedded in user data.

## Destroy the lab

From the same directory and with the same state and AWS identity:

```bash
terraform plan -destroy
terraform destroy
```

Review the destroy plan before confirming. Destruction removes the instance,
root volume, log groups and their retained logs, IAM resources, and network.
