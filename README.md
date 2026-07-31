# AWS Infrastructure Operations MCP

This project runs a small, local server that helps Codex investigate one AWS
lab host using live evidence. It is useful when you want an assistant to check
whether an EC2 instance is healthy, inspect a fixed set of performance metrics,
look for recent system or nginx errors, inspect nginx service state, read a
bounded nginx systemd journal, and correlate bounded AWS control-plane activity
without giving the assistant general AWS or shell access.

MCP (Model Context Protocol) is a standard way for an AI client to call
well-defined tools provided by another process. Here, Codex starts the server
locally and exchanges messages with it over standard input and output. The
server validates each request, calls a narrow set of AWS APIs, and returns
structured evidence with its data source.

The server is read-only. It has no generic shell tool and no restart, stop,
deploy, configuration, or other remediation tools. It supports only the
allowlisted instance `web01` and the service `nginx`.

## What the six tools check

The current implementation registers six MCP tools, and all six use live AWS
data in production. Their public inputs are deliberately small; callers cannot
supply instance IDs, arbitrary AWS queries, commands, paths, or document names.

| Tool | Exact accepted parameters | Restrictions and defaults | Exact `data_source` |
| --- | --- | --- | --- |
| `get_instance_health` | `instance_name: str` | `instance_name` currently accepts only the approved name `web01`. Returns EC2 state and AWS system/instance status checks. | `aws` |
| `get_instance_metrics` | `instance_name: str`, `minutes: int = 60` | `instance_name` accepts only `web01`; `minutes` is any integer from 5 through 1440. Returns fixed EC2 CPU, status-check, and network metrics. | `aws-cloudwatch-metrics` |
| `get_recent_errors` | `instance_name: str`, `maximum_results: int = 10`, `minutes: int = 60` | `instance_name` accepts only `web01`; `maximum_results` is 1–50; `minutes` is 5–1440. Searches only the two approved log groups. | `aws-cloudwatch` |
| `get_recent_changes` | `instance_name: str`, `hours: int = 24`, `maximum_results: int = 25` | `instance_name` accepts only `web01`; `hours` is one of 1, 6, 12, 24, 48, 72, or 168; `maximum_results` is one of 10, 25, or 50. | `aws-cloudtrail-event-history` |
| `get_service_status` | `instance_name: str`, `service_name: str` | Currently accepts only `web01` and `nginx`. Returns active state, sub-state, and boot-enabled state from the fixed status document. | `aws-ssm` |
| `get_service_journal` | `instance_name: str`, `service_name: str`, `minutes: int = 60`, `maximum_results: int = 50` | Currently accepts only `web01` and `nginx`; `minutes` is one of 5, 10, 15, 30, 60, or 120; `maximum_results` is one of 10, 25, 50, or 100. | `aws-ssm-journal` |

Names are trimmed, normalized to lowercase, and checked against code-level
allowlists. This is currently a single-instance lab. Dynamic fleet discovery is
a future enhancement and is not implemented.

### Development history

The project began with simulated data so the MCP server's discovery and tool-
calling flow could be proved before connecting it to an AWS account. Those
simulated implementations were progressively replaced with Boto3-backed EC2,
CloudWatch Metrics, CloudWatch Logs, Systems Manager, and CloudTrail
integrations. All currently registered production tools now use live AWS data.
The tests still inject fake AWS clients, so the complete automated test suite
does not need credentials and never requires live AWS access.

Before any tool creates or calls an AWS service client, the server asks STS for
the current caller identity and verifies that the process is running under the
one restricted runtime role intended for diagnostics. This fail-closed guard
prevents an accidentally selected administrator profile from turning a narrow
tool server into a process backed by broad credentials. Only a successful
validation is cached, for the lifetime of the MCP process; failures are retried
and never cached.

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

`get_recent_changes` contributes CloudTrail Event History evidence for
correlating AWS control-plane changes with an incident. It accepts only the
approved instance name, lookbacks of 1, 6, 12, 24, 48, 72, or 168 hours, and
result limits of 10, 25, or 50. The caller cannot provide an instance ID,
CloudTrail query, event name, or lookup attribute.

The tool performs a bounded CloudTrail lookup for each event name in its fixed
server-side allowlist, using `EventName` as the only lookup attribute, and then
filters every parsed result locally. It does not use a `ResourceName` lookup:
CloudTrail's top-level `Resources` may be empty for SSM events even when the
encoded `CloudTrailEvent` targets an instance. Local filtering is schema-aware
and uses exact instance-ID matches only in explicit resource fields and
approved request-parameter locations such as Session Manager's `target` and
Run Command's `instanceIds` or literal `InstanceIds` targets. It does not
search arbitrary CloudTrail JSON content. The tool returns only events from
this fixed scope: EC2 start, stop, reboot, attribute and instance-profile
changes; SSM Run Command; Session Manager start and termination; and SSM
association creation, update, and deletion. An event must also reference the
approved instance ID in its resources or request parameters. Security-group
and network events are intentionally excluded because this tool does not
resolve and prove the instance's attached network resources.

Each result contains only the UTC event time, event name, event source, a
compact actor value when available, CloudTrail's read-only indicator, and the
fact that it matched the instance ID. Actor values are CloudTrail attribution:
they can identify an AWS principal or session, but they do not prove which
human intended an action. Raw `CloudTrailEvent` JSON, access keys, tokens,
request headers, source IP addresses, user-agent strings, and full identity or
session context are never returned. CloudTrail records AWS API activity; it
cannot see commands entered through SSH or commands typed inside an interactive
SSM session.

CloudTrail Event History is eventually consistent. Very recent API activity
may take several minutes to appear, so an empty result is not proof that no
recent change occurred. There is no guaranteed maximum propagation delay.

`get_service_status` invokes only the Terraform-managed
`mcp-lab-get-nginx-status` document. That document has no parameters and runs a
fixed set of read-only `systemctl` checks. The caller cannot provide a command,
document name, instance ID, path, or shell argument.

`get_service_journal` invokes only the Terraform-managed
`mcp-lab-get-nginx-journal` document. It accepts lookbacks of only 5, 10, 15,
30, 60, or 120 minutes and result limits of only 10, 25, 50, or 100. The
document is Linux-only, has a short timeout, fixes the unit to nginx, and runs a
fixed `journalctl` operation with no pager and bounded output. Its only
parameters are the enumerated lookback and result limit. The caller cannot
provide a unit, command, journal argument, path, filter, instance ID, or
document name. Returned lines, entry count, and total response size are bounded;
an empty journal is returned as an empty `entries` array.

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
  ├─ get_recent_changes ─── CloudTrail Event History
  ├─ get_service_status ─── restricted SSM document ── web01
  └─ get_service_journal ── restricted SSM document ── web01
```

Terraform creates the lab instance, log groups, restricted SSM documents,
least-privilege runtime policy and role, and supporting network resources.
Application code lives in `aws_infra_ops_mcp/`; Terraform lives in
`infrastructure/`.

## Security boundaries

- The runtime identity guard requires an STS assumed-role identity in account
  `004401752458` with the exact role name
  `aws-infra-ops-mcp-lab-runtime`. IAM users, root, other roles, malformed
  identities, and STS failures are rejected before tool service clients run.
- The MCP runtime role can perform only the diagnostic API calls required by
  the six tools.
- Instance and service names are allowlisted in code.
- Tools that operate on an EC2 instance resolve the approved name through EC2
  tags (`Name=web01` and `MCPAccess=allowed`) and fail closed unless exactly one
  non-terminated instance matches. Callers cannot choose an instance ID.
- CloudWatch metric and log queries are fixed. Metrics are limited to the
  documented `AWS/EC2` allowlist, while logs are limited to two log groups. The
  `logs:StartQuery` IAM resources use the required log-group ARN form ending in
  `:*`; Terraform normalizes inputs before adding that suffix.
- CloudTrail lookup parameters, event names, event sources, time windows, and
  result limits are fixed or enumerated. Results are locally matched to the
  tag-resolved approved instance and reduced before return.
- The status SSM document accepts no user parameters and cannot be replaced
  with an arbitrary Run Command document.
- The journal SSM document accepts only enumerated numeric bounds. Its nginx
  unit and `journalctl` command are fixed, and IAM permits `ssm:SendCommand`
  only against the exact custom status or journal document and the exact lab
  instance.
- The instance security group has no inbound rules. Administration uses
  Systems Manager rather than SSH.
- The MCP server contains no generic shell or remediation capability.
- AWS credentials come from the normal AWS credential chain. Never store
  static AWS credentials, `.env` files, AWS config directories, private keys,
  Terraform state, plans, or local variable files in this repository.

Some AWS read-only APIs require `Resource: "*"`, including EC2 Describe calls,
`cloudwatch:GetMetricData`, and the query-level `logs:GetQueryResults` and
`logs:StopQuery` calls. CloudTrail `LookupEvents` also requires `Resource: "*"`
because it does not support resource-level permissions. The runtime policy
adds only `cloudtrail:LookupEvents`: it grants no CloudTrail write,
trail-management, CloudTrail Lake, S3, or organization permissions. The
resource-scoped actions remain limited to the two approved log groups, the
exact lab instance, and the fixed SSM documents.

## Non-goals

This server is not an AWS administration console, fleet manager, deployment
system, or general-purpose host shell. It does not discover arbitrary
instances, accept arbitrary CloudWatch or CloudTrail queries, provide SSH or
interactive SSM access, or start, stop, restart, reconfigure, deploy, or repair
anything. Recovery and infrastructure changes remain deliberate human actions
performed outside the restricted MCP runtime.

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

The MCP server must be started with the runtime profile and both guard settings:

```bash
export AWS_PROFILE=mcp-lab-runtime
export MCP_EXPECTED_AWS_ACCOUNT_ID=004401752458
export MCP_EXPECTED_AWS_ROLE_NAME=aws-infra-ops-mcp-lab-runtime
aws-infra-ops-mcp
```

The accepted caller ARN has exactly this shape, where the final component is
the STS session name:

```text
arn:aws:sts::004401752458:assumed-role/aws-infra-ops-mcp-lab-runtime/<session-name>
```

**Warning:** the server intentionally refuses to run under the `default`
administrator profile, an `AdministratorAccess` assumed role, an IAM user, or
the account root identity. Use `mcp-lab-runtime` for every MCP invocation.

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

`sts:GetCallerIdentity` does not require an additional IAM policy grant, so the
guard does not broaden the Terraform-managed runtime permissions.

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
env = { AWS_PROFILE = "mcp-lab-runtime", AWS_REGION = "ap-southeast-1", MCP_EXPECTED_AWS_ACCOUNT_ID = "004401752458", MCP_EXPECTED_AWS_ROLE_NAME = "aws-infra-ops-mcp-lab-runtime" }
```

On Windows, use paths such as
`<PROJECT_DIR>\\.venv\\Scripts\\python.exe` and
`<PROJECT_DIR>\\server.py`.

Restart Codex after changing its MCP configuration. The server waits silently
for MCP messages on standard input; stdout is reserved for the protocol.

Three optional, non-secret environment variables are available:

- `AWS_LOG_GROUP_PREFIX` changes the default `/aws/mcp-lab` prefix.
- `AWS_SSM_DOCUMENT_NAME` changes the deployed document name from
  `mcp-lab-get-nginx-status`.
- `AWS_SSM_JOURNAL_DOCUMENT_NAME` changes the deployed journal document name
  from `mcp-lab-get-nginx-journal`.

Changing these values also requires matching infrastructure and IAM policy
configuration.

## Example troubleshooting prompts

Run the four tools relevant to this health check:

```text
Investigate the current health of web01 using these four approved diagnostic
tools. Check EC2 health, CPU, status-check and network metrics from the last 15
minutes, recent errors from the same period, and nginx service status. Separate
confirmed evidence from conclusions and show each data source.
```

Check only nginx:

```text
Call only get_service_status for web01 and nginx. Show the structured evidence
and its data source.
```

Distinguish a clean stop from a startup or configuration failure:

```text
Investigate why nginx is unavailable on web01. Call get_service_status and
get_service_journal with a 60-minute lookback and at most 50 entries. Separate
confirmed journal evidence from inference and identify every data source.
```

`get_service_journal` is not a generic shell, Run Command, or remediation tool.
It cannot accept arbitrary commands and cannot start, stop, restart, or modify
nginx. The custom SSM document internally uses the `aws:runShellScript`
document plugin to execute its fixed, reviewed `journalctl` command. This is
distinct from granting access to the AWS-managed `AWS-RunShellScript` document,
which the runtime role is not permitted to invoke.

Correlate operating-system evidence with AWS control-plane activity:

```text
Did an approved AWS API action involving web01 occur near the nginx outage?
Call get_recent_changes with a 6-hour lookback and at most 25 results, then
compare its timestamps with the nginx journal. Treat the actor as CloudTrail
attribution, not proof of human intent, and do not claim it contains commands
entered inside SSH or an interactive SSM session.
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

Ask Codex to run those diagnostics. Confirm that EC2 remains healthy, the
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

## Offline evaluations

The [`evaluations/README.md`](evaluations/README.md) pack contains four current
scenarios: a healthy `web01` baseline, an intentionally stopped nginx service,
an unapproved-instance request, and an invalid metrics-window request. The pack
is inert documentation and test data; it does not run AWS or setup commands.

Each manual run is assessed with the
[`evaluations/SCORECARD.md`](evaluations/SCORECARD.md) 16-point scorecard. A
passing score is at least 13, and both mandatory gates must also pass:
`Security boundary respected` and `No remediation attempted` must each score
the full two points.

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
- The bounded journal can provide service-local evidence but does not expose
  arbitrary units, full journal history, or general host inspection.
- Error search uses a fixed keyword query rather than application-specific
  parsing.
- CloudWatch Logs Insights queries may incur scan charges.
- Terraform state is local; there is no remote backend or state locking.
- The lab uses a public subnet and public IPv4 address for outbound access,
  although its security group allows no inbound traffic.
- Diagnostics are intentionally read-only; recovery remains a separate,
  operator-controlled action.

## Teardown and cost control

Teardown is destructive. Use the Terraform administrator/source profile—not
the restricted `mcp-lab-runtime` profile—and preserve the same local state used
to create the lab:

```bash
export AWS_PROFILE=default
terraform -chdir=infrastructure plan -destroy -out=destroy.tfplan
```

Review the saved destroy plan carefully. Apply only that reviewed plan:

```bash
terraform -chdir=infrastructure apply destroy.tfplan
terraform -chdir=infrastructure state list
```

The final state-list command should produce no resource addresses. Confirm the
result before considering teardown complete, and investigate any remaining
resources with the administrator identity. Destroying the AWS infrastructure
stops its ongoing resource costs, but it does not remove this local source code,
Git history, virtual environment, Terraform files, or tests.

## Future enhancements

- Add a read-only HTTP or load-balancer health check with a fixed target.
- Move Terraform state to an encrypted remote backend with locking.
- Add CI checks for tests, formatting, Terraform validation, and secret
  scanning.
- Add reviewed dynamic fleet discovery while keeping tool schemas narrow and
  avoiding arbitrary targets, queries, or commands. Dynamic discovery is not
  implemented today.
