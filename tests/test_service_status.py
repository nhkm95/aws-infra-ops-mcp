from collections.abc import Iterator
from datetime import datetime, timezone
import json
from typing import Any

from botocore.exceptions import ClientError
import pytest

from aws_infra_ops_mcp.tools.service_status import (
    DEFAULT_SSM_DOCUMENT_NAME,
    MAX_OUTPUT_CHARS,
    inspect_service_status,
)

NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)


class FakeEc2:
    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.instances = instances
        self.describe_instances_request: dict[str, Any] | None = None

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_instances_request = kwargs
        return {"Reservations": [{"Instances": self.instances}]}


class FakeSsm:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses: Iterator[dict[str, Any] | Exception] = iter(responses)
        self.send_request: dict[str, Any] | None = None
        self.invocation_requests: list[dict[str, Any]] = []

    def send_command(self, **kwargs: Any) -> dict[str, Any]:
        self.send_request = kwargs
        return {"Command": {"CommandId": "command-123"}}

    def get_command_invocation(self, **kwargs: Any) -> dict[str, Any]:
        self.invocation_requests.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def instance(instance_id: str = "i-123") -> dict[str, Any]:
    return {"InstanceId": instance_id, "State": {"Name": "running"}}


def output(
    active_state: str = "active",
    sub_state: str = "running",
    enabled_at_boot: bool = True,
) -> str:
    return json.dumps(
        {
            "service_name": "nginx",
            "active_state": active_state,
            "sub_state": sub_state,
            "enabled_at_boot": enabled_at_boot,
        },
        separators=(",", ":"),
    )


def success(status_output: str | None = None) -> dict[str, Any]:
    return {
        "Status": "Success",
        "StandardOutputContent": status_output or output(),
    }


def ticking_clock(values: list[float]):
    times = iter(values)
    return lambda: next(times)


def inspect(
    ssm: FakeSsm,
    *,
    ec2: FakeEc2 | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return inspect_service_status(
        "web01",
        "nginx",
        ec2 or FakeEc2([instance()]),
        ssm,
        monotonic=kwargs.pop("monotonic", lambda: 0.0),
        sleep=kwargs.pop("sleep", lambda _: None),
        clock=lambda: NOW,
        **kwargs,
    )


def test_nginx_active_running_and_enabled() -> None:
    result = inspect(FakeSsm([success()]))

    assert result == {
        "instance_name": "web01",
        "instance_id": "i-123",
        "service_name": "nginx",
        "active_state": "active",
        "sub_state": "running",
        "enabled_at_boot": True,
        "data_source": "aws-ssm",
        "checked_at": "2026-07-30T03:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("active_state", "sub_state"),
    [("inactive", "dead"), ("failed", "failed")],
)
def test_nginx_not_active_and_disabled(
    active_state: str, sub_state: str
) -> None:
    result = inspect(
        FakeSsm([success(output(active_state, sub_state, False))])
    )

    assert result["active_state"] == active_state
    assert result["sub_state"] == sub_state
    assert result["enabled_at_boot"] is False


def test_rejects_unapproved_instance_before_aws_calls() -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([])

    with pytest.raises(ValueError, match="not approved"):
        inspect_service_status("database01", "nginx", ec2, ssm)

    assert ec2.describe_instances_request is None
    assert ssm.send_request is None


def test_rejects_unapproved_service_before_aws_calls() -> None:
    ec2 = FakeEc2([instance()])
    ssm = FakeSsm([])

    with pytest.raises(ValueError, match="not approved"):
        inspect_service_status("web01", "sshd", ec2, ssm)

    assert ec2.describe_instances_request is None
    assert ssm.send_request is None


def test_send_command_is_fixed_and_targets_resolved_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_SSM_DOCUMENT_NAME", raising=False)
    ec2 = FakeEc2([instance("i-resolved")])
    ssm = FakeSsm([success()])

    inspect(ssm, ec2=ec2)

    assert ec2.describe_instances_request == {
        "Filters": [
            {"Name": "tag:Name", "Values": ["web01"]},
            {"Name": "tag:MCPAccess", "Values": ["allowed"]},
            {
                "Name": "instance-state-name",
                "Values": [
                    "pending",
                    "running",
                    "shutting-down",
                    "stopping",
                    "stopped",
                ],
            },
        ]
    }
    assert ssm.send_request == {
        "InstanceIds": ["i-resolved"],
        "DocumentName": DEFAULT_SSM_DOCUMENT_NAME,
        "TimeoutSeconds": 30,
        "MaxConcurrency": "1",
        "MaxErrors": "0",
        "Comment": "Read-only nginx status check for MCP diagnostics",
    }
    assert "Parameters" not in ssm.send_request


def test_initial_invocation_does_not_exist_is_retried() -> None:
    not_ready = ClientError(
        {"Error": {"Code": "InvocationDoesNotExist", "Message": "not ready"}},
        "GetCommandInvocation",
    )
    ssm = FakeSsm([not_ready, success()])

    result = inspect(
        ssm,
        monotonic=ticking_clock([0.0, 0.1, 0.2]),
    )

    assert result["active_state"] == "active"
    assert len(ssm.invocation_requests) == 2


@pytest.mark.parametrize("status", ["Failed", "TimedOut"])
def test_terminal_ssm_invocation(status: str) -> None:
    ssm = FakeSsm(
        [
            {
                "Status": status,
                "StandardErrorContent": "short diagnostic",
            }
        ]
    )

    with pytest.raises(
        RuntimeError, match=f"status {status}: short diagnostic"
    ):
        inspect(ssm)


def test_local_polling_deadline() -> None:
    ssm = FakeSsm([{"Status": "InProgress"}])

    with pytest.raises(RuntimeError, match="local polling timeout"):
        inspect(
            ssm,
            monotonic=ticking_clock([0.0, 15.0]),
        )


@pytest.mark.parametrize(
    "status_output",
    [
        "not-json",
        '{"service_name":"nginx"}',
        output() + "\n" + output(),
    ],
)
def test_malformed_json_output(status_output: str) -> None:
    with pytest.raises(RuntimeError, match="malformed|unexpected fields"):
        inspect(FakeSsm([success(status_output)]))


def test_oversized_output() -> None:
    with pytest.raises(RuntimeError, match="unexpectedly large"):
        inspect(FakeSsm([success("x" * (MAX_OUTPUT_CHARS + 1))]))
