# AWS Infrastructure Operations MCP

This project runs a small, local server that helps Codex investigate one AWS
lab host using live evidence. It is useful when you want an assistant to check
whether an EC2 instance is healthy, inspect a fixed set of performance metrics,
look for recent system or nginx errors, and inspect nginx service state without
giving the assistant general AWS or shell access.

MCP (Model Context Protocol) is a standard way for an AI client to call
well-defined tools provided by another process. Here, Codex starts the server
locally and exchanges messages with it over standard input and output. The
server validates each request, calls a narrow set of AWS APIs, and returns
structured evidence with its data source.

The server is read-only. It has no generic shell tool and no restart, stop,
deploy, configuration, or other remediation tools. It supports only the
allowlisted instance `web01` and the service `nginx`.

## What the four tools check

| Tool | AWS source | What it returns |
| --- | --- | --- |
| `get_instance_health` | EC2 | Instance state plus AWS system and instance status checks. |
| `get_instance_metrics` | CloudWatch metrics | CPU average/maximum, status-check maximums, and total network bytes over a bounded lookback. |
| `get_recent_errors` | CloudWatch Logs Insights | A bounded set of recent error-related events from the approved system and nginx log groups. |
| `get_service_status` | Systems Manager | nginx active state, sub-state, and boot-enabled state from a fixed, restricted SSM document. |

Every response identifies its source, including `aws-cloudwatch-metrics` for
instance metrics. Tests inject fake AWS clients, so the automated test suite
makes no AWS calls.

`get_instance_metrics` accepts only an approved instance name and a lookback of
5–1440 minutes (60 minutes by default). It uses five-minute periods and the
fixed `AWS/EC2` allowlist: `CPUUtilization` Average and Maximum;
`StatusCheckFailed`, `StatusCheckFailed_Instance`, and
`StatusCheckFailed_System` Maximum; and `NetworkIn` and `NetworkOut` Sum. The
caller cannot supply an instance ID, namespace, metric, dimension, statistic,
period, or CloudWatch query. Missing datapoints are returned as `null`, not
invented zero values.

`get_recent_errors` accepts a lookback of 5–1440 minutes and a result limit of
1–50. The caller cannot provide log-group names or Logs Insights query text.
The server always queries:

```text
/aws/mcp-lab/web01/system
/aws/mcp-lab/web01/nginx
```

`get_service_status` invokes only the Terraform-managed
`mcp-lab-get-nginx-status` document. That document has no parameters and runs a
fixed set of read-only `systemctl` checks. The caller cannot provide a command,
document name, instance ID, path, or shell argument.

## Architecture

```text
Codex
  │  MCP over local stdio
  ▼
server.py
  │
  ├─ get_instance_health ── EC2 status APIs
  ├─ get_instance_metrics ─ CloudWatch GetMetricData
  ├─ get_recent_errors ──── CloudWatch Logs Insights
  └─ get_service_status ─── restricted SSM document ── web01
```

Terraform creates the lab instance, log groups, restricted SSM document,
least-privilege runtime policy and role, and supporting network resources.
Application code lives in `aws_infra_ops_mcp/`; Terraform lives in
`infrastructure/`.

## Security boundaries

- The MCP runtime role can perform only the diagnostic API calls required by
  the four tools.
- Instance and service names are allowlisted in code.
- CloudWatch metric and log queries are fixed. Metrics are limited to the
  documented `AWS/EC2` allowlist, while logs are limited to two log groups. The
  `logs:StartQuery` IAM resources use the required log-group ARN form ending in
  `:*`; Terraform normalizes inputs before adding that suffix.
- The SSM document accepts no user parameters and cannot be replaced with an
  arbitrary Run Command document.
- The instance security group has no inbound rules. Administration uses
  Systems Manager rather than SSH.
- The MCP server contains no generic shell or remediation capability.
- AWS credentials come from the normal AWS credential chain. Never store
  static AWS credentials, `.env` files, AWS config directories, private keys,
  Terraform state, plans, or local variable files in this repository.

Some AWS read-only APIs require `Resource: "*"`, including EC2 Describe calls,
`cloudwatch:GetMetricData`, and the query-level `logs:GetQueryResults` and
`logs:StopQuery` calls. The runtime policy grants only that single CloudWatch
metrics action—no write, alarm, or broad CloudWatch read permissions. The
resource-scoped actions remain limited to the two approved log groups, the
exact lab instance, and the fixed SSM document.

## Prerequisites

- Python 3.11 or newer
- Terraform 1.6 or newer
- Codex with MCP server support
- AWS CLI and the Session Manager plugin for administrative checks
- An AWS Identity Center or other source profile with permission to deploy the
  Terraform configuration
- An AWS Region configured; the example defaults to `ap-southeast-1`

The target instance must have these tags:

| Tag | Value |
| --- | --- |
| `Name` | `web01` |
| `MCPAccess` | `allowed` |

## Local setup

On Linux, macOS, or WSL:

```bash
cd <PROJECT_DIR>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On Windows PowerShell:

```powershell
Set-Location <PROJECT_DIR>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, call `.venv\Scripts\python.exe` directly.

## Running tests

With the virtual environment active:

```bash
python -m pytest -v
```

Useful Terraform checks that do not change AWS are:

```bash
terraform -chdir=infrastructure fmt -check -recursive
terraform -chdir=infrastructure validate
```

## Two AWS profiles

Use separate profiles for deployment and diagnostics:

- `default` is the source/administrator profile. Terraform uses it to create,
  update, and destroy the lab.
- `mcp-lab-runtime` assumes the restricted runtime role. Only the MCP server
  should use it for diagnostics.

Do not run Terraform with `mcp-lab-runtime`; its limited permissions are
intentional and are not enough to refresh or manage Terraform resources.

Configure the assumed-role profile in your user-level AWS config, outside this
repository:

```ini
[profile mcp-lab-runtime]
role_arn = arn:aws:iam::<AWS_ACCOUNT_ID>:role/aws-infra-ops-mcp-lab-runtime
source_profile = default
role_session_name = aws-infra-ops-mcp
duration_seconds = 3600
region = ap-southeast-1
```

For an Identity Center deployment, set Terraform's
`trusted_sso_role_arn_pattern` to a placeholder-based value such as:

```text
arn:aws:iam::<AWS_ACCOUNT_ID>:role/aws-reserved/sso.amazonaws.com/<AWS_REGION>/AWSReservedSSO_<PERMISSION_SET>_<IDENTITY_CENTER_ROLE_SUFFIX>
```

Verify each profile outside the test suite:

```bash
aws sts get-caller-identity --profile default
aws sts get-caller-identity --profile mcp-lab-runtime
```

The runtime result should contain
`assumed-role/aws-infra-ops-mcp-lab-runtime/`.

## Terraform deployment

Terraform uses local state. Keep `terraform.tfstate` secure and backed up; it is
ignored by Git. Run deployment commands with the `default` profile:

```bash
export AWS_PROFILE=default
terraform -chdir=infrastructure init
terraform -chdir=infrastructure fmt -check -recursive
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure plan -out=tfplan
terraform -chdir=infrastructure apply tfplan
```

Copy `infrastructure/terraform.tfvars.example` to
`infrastructure/terraform.tfvars` only when overrides are needed. The local
file is ignored and must not be committed.

Applying the configuration creates billable AWS resources, including EC2, EBS,
a public IPv4 address, and CloudWatch Logs. Review the plan and current AWS
pricing before applying. For more infrastructure detail, see
[`infrastructure/README.md`](infrastructure/README.md).

## Connecting the server to Codex

Configure Codex to start the virtual-environment Python executable with the
repository's `server.py`. Use absolute paths and set the restricted AWS profile
for the child process:

```toml
[mcp_servers.aws-infra-ops-lab]
command = "<PROJECT_DIR>/.venv/bin/python"
args = ["<PROJECT_DIR>/server.py"]
env = { AWS_PROFILE = "mcp-lab-runtime", AWS_REGION = "ap-southeast-1" }
```

On Windows, use paths such as
`<PROJECT_DIR>\\.venv\\Scripts\\python.exe` and
`<PROJECT_DIR>\\server.py`.

Restart Codex after changing its MCP configuration. The server waits silently
for MCP messages on standard input; stdout is reserved for the protocol.

Two optional, non-secret environment variables are available:

- `AWS_LOG_GROUP_PREFIX` changes the default `/aws/mcp-lab` prefix.
- `AWS_SSM_DOCUMENT_NAME` changes the deployed document name from
  `mcp-lab-get-nginx-status`.

Changing these values also requires matching infrastructure and IAM policy
configuration.

## Example troubleshooting prompts

Run all four diagnostics:

```text
Investigate the current health of web01 using all four approved diagnostic
tools. Check EC2 health, CPU, status-check and network metrics from the last 15
minutes, recent errors from the same period, and nginx service status. Separate
confirmed evidence from conclusions and show each data source.
```

Check only nginx:

```text
Call only get_service_status for web01 and nginx. Show the structured evidence
and its data source.
```

Correlate an outage:

```text
The web service on web01 appears unavailable. Correlate EC2 health, errors from
the last 15 minutes, and nginx status. Identify the most likely cause using
only confirmed evidence.
```

## Controlled nginx incident test

This test deliberately stops nginx. Run it only in the disposable lab and use
the `default` administrator profile—not the MCP runtime profile.

Start a Session Manager session:

```bash
export AWS_PROFILE=default
aws ssm start-session --target "$(terraform -chdir=infrastructure output -raw instance_id)"
```

Inside the session, stop nginx and write a recognizable system-log event:

```bash
sudo systemctl stop nginx
logger "MCP-LAB ERROR: nginx intentionally stopped for diagnostic validation"
```

Ask Codex to run all four diagnostics. Confirm that EC2 remains healthy, the
metrics remain diagnostic-only, the log event appears, and nginx reports
`inactive`/`dead`.

Always restore the service before ending the test:

```bash
sudo systemctl start nginx
systemctl is-active nginx
curl --fail http://127.0.0.1/
```

The expected final results are `active` and a successful local HTTP response.
The MCP server cannot perform this stop or restoration.

## Common troubleshooting

**Terraform returns AccessDenied while using `mcp-lab-runtime`**

Switch to `AWS_PROFILE=default`. The runtime role is intentionally unable to
administer or fully refresh Terraform-managed resources.

**CloudWatch `StartQuery` returns AccessDenied**

Apply the current Terraform policy with the administrator profile. The two
approved log-group resource ARNs must end in `:*`, not the raw log-group ARN.

**The MCP server cannot assume its runtime role**

Check the `source_profile`, account ID, Region, runtime role ARN, and
`trusted_sso_role_arn_pattern`. Identity Center role suffixes can change when a
permission-set assignment is recreated.

**`get_service_status` times out or returns an SSM error**

Confirm that `web01` is online in Systems Manager, the fixed document exists,
and the runtime policy references the exact instance and document ARNs.

**Recent errors are empty**

Check that the CloudWatch Agent is running, both approved log groups have a
current stream, and the requested lookback covers the event. An empty result
means no matching events were returned; it does not test HTTP reachability.

**Codex does not show the tools**

Use absolute paths in the MCP configuration, confirm the virtual environment
contains the package, and restart Codex after configuration changes.

## Current limitations

- Only `web01` and `nginx` are supported.
- There is no HTTP reachability or end-to-end request tool.
- Error search uses a fixed keyword query rather than application-specific
  parsing.
- CloudWatch Logs Insights queries may incur scan charges.
- Terraform state is local; there is no remote backend or state locking.
- The lab uses a public subnet and public IPv4 address for outbound access,
  although its security group allows no inbound traffic.
- Diagnostics are intentionally read-only; recovery remains a separate,
  operator-controlled action.

## Sensible next steps

- Add a read-only HTTP or load-balancer health check with a fixed target.
- Add CloudWatch metrics and selected CloudTrail events as bounded tools.
- Move Terraform state to an encrypted remote backend with locking.
- Add CI checks for tests, formatting, Terraform validation, and secret
  scanning.
- Generalize allowlists through reviewed configuration while keeping tool
  schemas narrow and avoiding arbitrary queries or commands.
