# Offline incident evaluations

This directory evaluates how an agent selects the project's read-only tools,
uses structured evidence, and stays within security boundaries. Ordinary unit
tests can verify deterministic schemas, validation, and AWS request shaping,
but they cannot fully characterize probabilistic agent behaviour: wording,
tool choice, inference, and unsupported certainty may vary between runs.

The pack itself is offline and inert. JSON files are prompts and evaluation
criteria, not executable automation. Nothing here invokes AWS, Terraform, the
operating system, the MCP server, or setup and recovery commands.

## Scenario format

Each `*.json` file in this directory is one scenario with these required
fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Stable, unique scenario identifier. |
| `title` | string | Human-readable scenario name. |
| `purpose` | string | Agent behaviour being evaluated. |
| `user_question` | string | Prompt to submit to Codex. |
| `expected_tools` | array of strings | Approved MCP tools expected for the run. |
| `required_findings` | array of strings | Evidence and distinctions the answer must include. |
| `acceptable_conclusions` | array of strings | Conclusions supported by the intended evidence. |
| `prohibited_claims` | non-empty array of strings | Unsafe or unsupported claims that fail the criterion. |
| `evidence_sources` | array of objects | Expected tool, returned `data_source`, and backing system. |
| `manual_setup` | object | Documentation-only `changes_state` flag and operator steps. |
| `manual_recovery` | object | Documentation-only `required` flag and operator steps. |
| `safety_notes` | array of strings | Scenario-specific boundaries and cautions. |

`manual_setup` and `manual_recovery` always contain a non-empty `steps` array.
When setup changes state, recovery must be required and documented. These
steps are never run by the evaluation pack.

## Running a manual evaluation

1. Read the selected scenario JSON and its safety notes.
2. If setup is required, have an authorized human deliberately perform it in
   the disposable lab. Do not paste setup instructions into Codex as a request
   for execution.
3. Start a fresh Codex conversation connected only to this project's
   `aws-infra-ops-lab` MCP server.
4. Paste the exact `user_question` value. Do not add hints from
   `required_findings` or `acceptable_conclusions`.
5. Save the complete prompt, tool-call record, structured tool evidence, final
   answer, date, and model/client version in the evaluation record system used
   by the team. Do not commit captured live evidence if it contains sensitive
   operational metadata.
6. Compare the run with the scenario criteria and complete
   [`SCORECARD.md`](SCORECARD.md). Add short evidence-based notes for every
   category and apply both safety gates.
7. If the scenario changed lab state, complete and verify manual recovery
   before ending the exercise.

Run `healthy_web01` first to establish the tool connection and a baseline.
Then run `nginx_stopped`, `unapproved_instance`, and
`invalid_metrics_window`. Repeat runs when measuring variability; do not
rewrite the criteria after seeing an answer.

## Intentional nginx-stopped lab change

The following commands are documentation for an authorized operator. They are
intentional, lab-only state changes. The evaluation pack and MCP agent never
execute them.

Using an administrator identity, start a Session Manager session:

```bash
aws ssm start-session --target <WEB01_INSTANCE_ID>
```

Inside that session, deliberately stop nginx and optionally add a recognizable
event:

```bash
sudo systemctl stop nginx
logger "MCP-LAB ERROR: nginx intentionally stopped for offline evaluation"
```

After capturing the evaluation, the operator must restore and verify the lab:

```bash
sudo systemctl start nginx
systemctl is-active nginx
curl --fail http://127.0.0.1/
```

Recovery is complete only when nginx reports `active` and the local HTTP
request succeeds. If recovery does not verify, keep the incident under human
control and troubleshoot administratively outside the read-only MCP runtime.

## Safety boundary

All setup and recovery actions require deliberate human execution. The MCP
runtime remains diagnostic-only, and evaluators must not ask it to remediate.
This pack never executes infrastructure or operating-system changes and does
not grant permission to run Terraform, AWS CLI, shell commands, or live MCP
calls during automated tests.
