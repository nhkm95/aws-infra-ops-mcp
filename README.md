# AWS Infrastructure Operations MCP

A small local Model Context Protocol (MCP) server for learning how an AI client
can perform evidence-based AWS infrastructure troubleshooting.

This version is deliberately narrow and read-only:

- It runs locally over MCP stdio.
- It exposes three narrow, read-only diagnostic tools.
- Instance health comes from AWS EC2; service status and recent errors remain simulated.
- It accepts only the allowlisted instance `web01` and service `nginx`.
- It contains no credentials or remediation actions.

## Architecture

```text
MCP-compatible AI client
          |
          | MCP over stdio
          v
      server.py
          |
          v
 aws_infra_ops_mcp/app.py
          |
          v
 tools/instance_health.py  --> Boto3 EC2 client (read-only)
 tools/simulated.py        --> simulated service/error results
```

The MCP protocol registration is kept in `app.py`; validation rules live in
`policy.py`; AWS client construction lives in `aws.py`; and diagnostic logic
lives under `tools/`. The EC2 business logic accepts an injected client so tests
remain offline while the public MCP schema stays unchanged.

## Available tools

| Tool | Purpose |
| --- | --- |
| `get_instance_health` | Returns AWS EC2 state and system/instance status checks. |
| `get_recent_errors` | Returns a bounded list of simulated system/application errors. |
| `get_service_status` | Returns the simulated state of an approved service. |

Results identify their origin with `"data_source": "aws"` or
`"data_source": "simulated"`.

## Requirements

- Python 3.11 or newer
- An MCP-compatible client
- An AWS Region and credentials available through Boto3's normal AWS
  configuration and credential-provider chains

Do not place credentials in this repository. The server does not read custom
credential files or select a hard-coded profile, account, or Region.

The target EC2 instance must have both tags:

| Tag | Required value |
| --- | --- |
| `Name` | The allowlisted instance name, currently `web01` |
| `MCPAccess` | `allowed` |

The credentials need only these read-only IAM permissions:

```text
ec2:DescribeInstances
ec2:DescribeInstanceStatus
```

## Set up on Windows PowerShell

From this project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, the environment can be used without activating
it:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Set up on Linux, macOS, or WSL

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Run the tests

With the virtual environment active:

```powershell
python -m pytest
```

## Run the server

```powershell
python server.py
```

The process will wait silently for MCP messages over standard input. Press
`Ctrl+C` to stop it. Do not use ordinary `print()` calls in the server because
stdout carries the MCP protocol.

After an editable install, this equivalent command is also available:

```powershell
aws-infra-ops-mcp
```

## Example MCP client configuration

Use absolute paths for both the virtual-environment Python executable and
`server.py`.

```toml
[mcp_servers.aws-infra-ops-lab]
command = "C:\\absolute\\path\\to\\aws-infra-ops-mcp\\.venv\\Scripts\\python.exe"
args = ["C:\\absolute\\path\\to\\aws-infra-ops-mcp\\server.py"]
```

Restart the client after changing its MCP configuration. Then try:

```text
Why is web01 unhealthy? Gather health, recent errors, and nginx service status.
```

Instance state and status-check evidence is read live from EC2. The nginx and
recent-error evidence in this example remains simulated.

## Integration scope

Only `get_instance_health` currently calls AWS. Future phases may add read-only
integrations for:

- CloudWatch metrics
- CloudWatch Logs Insights
- CloudTrail event history
- Systems Manager managed-node status

The public tool schemas should remain narrow and read-only. Do not add generic
shell execution, arbitrary query text, credentials in files, or remediation
tools during that phase.
