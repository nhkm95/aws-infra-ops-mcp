from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
import pytest

import aws_infra_ops_mcp.tools.service_journal as journal_module
from aws_infra_ops_mcp.policy import (
    ALLOWED_JOURNAL_LOOKBACK_MINUTES,
    ALLOWED_JOURNAL_RESULT_LIMITS,
)
from aws_infra_ops_mcp.tools.service_journal import (
    DEFAULT_SSM_JOURNAL_DOCUMENT_NAME,
    MAX_LINE_CHARS,
    MAX_RAW_OUTPUT_CHARS,
    inspect_service_journal,
)

NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)
TERRAFORM_DOCUMENT = (
    Path(__file__).parents[1]
    / "infrastructure"
    / "modules"
    / "mcp_readonly"
    / "main.tf"
)


class FakeEc2:
    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.instances = instances
        self.request: dict[str, Any] | None = None

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {"Reservations": [{"Instances": self.instances}]}


class FakeSsm:
    def __init__(
        self,
        responses: list[dict[str, Any] | Exception],
        *,
        send_error: Exception | None = None,
    ) -> None:
        self.responses: Iterator[dict[str, Any] | Exception] = iter(responses)
        self.send_error = send_error
        self.send_request: dict[str, Any] | None = None
        self.invocation_requests: list[dict[str, Any]] = []

    def send_command(self, **kwargs: Any) -> dict[str, Any]:
        self.send_request = kwargs
        if self.send_error:
            raise self.send_error
        return {"Command": {"CommandId": "command-123"}}

    def get_command_invocation(self, **kwargs: Any) -> dict[str, Any]:
        self.invocation_requests.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def instance(instance_id: str = "i-123") -> dict[str, Any]:
    return {"InstanceId": instance_id, "State": {"Name": "running"}}


def success(output: str = "") -> dict[str, Any]:
    return {"Status": "Success", "StandardOutputContent": output}


def ticking_clock(values: list[float]):
    times = iter(values)
    return lambda: next(times)


def inspect(
    ssm: FakeSsm,
    *,
    minutes: int = 60,
    maximum_results: int = 50,
    ec2: FakeEc2 | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return inspect_service_journal(
        "web01",
        "nginx",
        minutes,
        maximum_results,
        ec2 or FakeEc2([instance()]),
        ssm,
        monotonic=kwargs.pop("monotonic", lambda: 0.0),
        sleep=kwargs.pop("sleep", lambda _: None),
        clock=lambda: NOW,
        **kwargs,
    )


def test_successful_journal_retrieval_and_fixed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_SSM_JOURNAL_DOCUMENT_NAME", raising=False)
    ec2 = FakeEc2([instance("i-resolved")])
    ssm = FakeSsm([success("first line\nsecond line\n")])

    result = inspect(ssm, ec2=ec2)

    assert result == {
        "instance_name": "web01",
        "instance_id": "i-resolved",
        "service_name": "nginx",
        "lookback_minutes": 60,
        "maximum_results": 50,
        "result_count": 2,
        "entries": ["first line", "second line"],
        "truncated": False,
        "command_status": "Success",
        "data_source": "aws-ssm-journal",
        "checked_at": "2026-07-30T03:00:00+00:00",
    }
    assert ssm.send_request == {
        "InstanceIds": ["i-resolved"],
        "DocumentName": DEFAULT_SSM_JOURNAL_DOCUMENT_NAME,
        "Parameters": {
            "lookbackMinutes": ["60"],
            "maximumResults": ["50"],
        },
        "TimeoutSeconds": 30,
        "MaxConcurrency": "1",
        "MaxErrors": "0",
        "Comment": "Read-only bounded nginx journal for MCP diagnostics",
    }


@pytest.mark.parametrize("minutes", sorted(ALLOWED_JOURNAL_LOOKBACK_MINUTES))
def test_each_approved_lookback(minutes: int) -> None:
    result = inspect(FakeSsm([success()]), minutes=minutes)
    assert result["lookback_minutes"] == minutes


@pytest.mark.parametrize("maximum", sorted(ALLOWED_JOURNAL_RESULT_LIMITS))
def test_each_approved_result_limit(maximum: int) -> None:
    result = inspect(FakeSsm([success()]), maximum_results=maximum)
    assert result["maximum_results"] == maximum


@pytest.mark.parametrize("minutes", [0, 4, 6, 121, 1440])
def test_rejected_lookbacks_make_no_aws_calls(minutes: int) -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([])
    with pytest.raises(ValueError, match="minutes must be one of"):
        inspect_service_journal("web01", "nginx", minutes, 50, ec2, ssm)
    assert ec2.request is None
    assert ssm.send_request is None


@pytest.mark.parametrize("maximum", [0, 9, 11, 51, 101])
def test_rejected_result_limits_make_no_aws_calls(maximum: int) -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([])
    with pytest.raises(ValueError, match="maximum_results must be one of"):
        inspect_service_journal("web01", "nginx", 60, maximum, ec2, ssm)
    assert ec2.request is None
    assert ssm.send_request is None


def test_rejects_unapproved_instance_and_service_before_aws_calls() -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([])
    with pytest.raises(ValueError, match="not approved"):
        inspect_service_journal("database01", "nginx", 60, 50, ec2, ssm)
    with pytest.raises(ValueError, match="not approved"):
        inspect_service_journal("web01", "sshd", 60, 50, ec2, ssm)
    assert ec2.request is None
    assert ssm.send_request is None


def test_empty_journal_returns_empty_entries() -> None:
    result = inspect(FakeSsm([success()]))
    assert result["entries"] == []
    assert result["result_count"] == 0
    assert result["truncated"] is False


def test_output_is_truncated_by_entry_count_and_line_length() -> None:
    lines = ["x" * (MAX_LINE_CHARS + 20), *(f"line {n}" for n in range(10))]
    result = inspect(
        FakeSsm([success("\n".join(lines))]),
        maximum_results=10,
    )
    assert result["result_count"] == 10
    assert len(result["entries"][0]) == MAX_LINE_CHARS
    assert result["truncated"] is True


@pytest.mark.parametrize("output", [None, "valid\x00invalid"])
def test_malformed_output(output: Any) -> None:
    invocation = {"Status": "Success", "StandardOutputContent": output}
    with pytest.raises(RuntimeError, match="invalid type|malformed"):
        inspect(FakeSsm([invocation]))


def test_oversized_output_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="unexpectedly large"):
        inspect(FakeSsm([success("x" * (MAX_RAW_OUTPUT_CHARS + 1))]))


@pytest.mark.parametrize("status", ["Failed", "TimedOut", "Undeliverable"])
def test_failed_command_is_controlled(status: str) -> None:
    invocation = {
        "Status": status,
        "StandardErrorContent": "bounded diagnostic",
    }
    with pytest.raises(
        RuntimeError,
        match=f"status {status}: bounded diagnostic",
    ):
        inspect(FakeSsm([invocation]))


def test_local_polling_timeout() -> None:
    with pytest.raises(RuntimeError, match="local polling timeout"):
        inspect(
            FakeSsm([{"Status": "InProgress"}]),
            monotonic=ticking_clock([0.0, 15.0]),
        )


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("AccessDeniedException", "AccessDeniedException"),
        ("InvalidInstanceId", "InvalidInstanceId"),
    ],
)
def test_access_denied_and_offline_instance_are_controlled(
    code: str,
    message: str,
) -> None:
    error = ClientError(
        {"Error": {"Code": code, "Message": "request rejected"}},
        "SendCommand",
    )
    with pytest.raises(RuntimeError, match=message):
        inspect(FakeSsm([], send_error=error))


def test_public_wrapper_uses_injected_factories_without_live_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([success("entry")])
    monkeypatch.setattr(journal_module, "create_ec2_client", lambda: ec2)
    monkeypatch.setattr(journal_module, "create_ssm_client", lambda: ssm)

    result = journal_module.get_service_journal("web01", "nginx")

    assert result["entries"] == ["entry"]


def test_terraform_document_and_iam_are_narrowly_scoped() -> None:
    terraform = TERRAFORM_DOCUMENT.read_text(encoding="utf-8")

    assert 'name            = "mcp-lab-get-nginx-journal"' in terraform
    assert "platformType" in terraform
    assert 'allowedValues     = ["5", "10", "15", "30", "60", "120"]' in terraform
    assert 'allowedValues     = ["10", "25", "50", "100"]' in terraform
    assert "journalctl --unit=nginx" in terraform
    assert "--no-pager" in terraform
    assert 'action = "aws:runShellScript"' in terraform
    assert "AWS-RunShellScript" not in terraform
    assert "aws_ssm_document.nginx_journal.arn" in terraform
    assert 'actions = ["ssm:SendCommand"]' in terraform


def test_existing_iam_policy_identity_and_attachment_remain_stable() -> None:
    terraform = TERRAFORM_DOCUMENT.read_text(encoding="utf-8")

    assert 'resource "aws_iam_policy" "this"' in terraform
    assert "name        = var.name" in terraform
    assert "name_prefix" not in terraform
    assert (
        'description = "Read-only EC2, CloudWatch metrics and Logs lookup '
        'for the Infrastructure Operations MCP"'
    ) in terraform
    assert (
        'resource "aws_iam_role_policy_attachment" '
        '"runtime_diagnostics_readonly"'
    ) in terraform
    assert "policy_arn = aws_iam_policy.this.arn" in terraform
