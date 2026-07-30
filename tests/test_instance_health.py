from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from aws_infra_ops_mcp.tools.instance_health import inspect_instance_health

NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)


class FakeEc2:
    def __init__(
        self,
        instances: list[dict[str, Any]],
        *,
        statuses: list[dict[str, Any]] | None = None,
        region: str | None = "ap-southeast-1",
        error: Exception | None = None,
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self.instances = instances
        self.statuses = statuses or []
        self.error = error
        self.describe_instances_request: dict[str, Any] | None = None
        self.describe_status_request: dict[str, Any] | None = None

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_instances_request = kwargs
        if self.error:
            raise self.error
        return {"Reservations": [{"Instances": self.instances}]}

    def describe_instance_status(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_status_request = kwargs
        if self.error:
            raise self.error
        return {"InstanceStatuses": self.statuses}


def instance(state: str = "running", instance_id: str = "i-123") -> dict[str, Any]:
    return {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "PrivateIpAddress": "10.0.1.25",
        "Placement": {"AvailabilityZone": "ap-southeast-1a"},
    }


def test_healthy_running_instance() -> None:
    client = FakeEc2(
        [instance()],
        statuses=[
            {
                "SystemStatus": {"Status": "ok"},
                "InstanceStatus": {"Status": "ok"},
            }
        ],
    )

    result = inspect_instance_health(" WEB01 ", client, clock=lambda: NOW)

    assert result == {
        "instance_name": "web01",
        "instance_id": "i-123",
        "region": "ap-southeast-1",
        "state": "running",
        "system_status": "ok",
        "instance_status": "ok",
        "private_ip": "10.0.1.25",
        "availability_zone": "ap-southeast-1a",
        "data_source": "aws",
        "checked_at": "2026-07-30T03:00:00+00:00",
    }
    assert client.describe_instances_request == {
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
    assert client.describe_status_request == {
        "InstanceIds": ["i-123"],
        "IncludeAllInstances": True,
    }


def test_stopped_instance_without_status_checks() -> None:
    result = inspect_instance_health(
        "web01", FakeEc2([instance("stopped")]), clock=lambda: NOW
    )

    assert result["state"] == "stopped"
    assert result["system_status"] == "not-applicable"
    assert result["instance_status"] == "not-applicable"


def test_no_matching_instance() -> None:
    with pytest.raises(LookupError, match="No non-terminated"):
        inspect_instance_health("web01", FakeEc2([]))


def test_multiple_matching_instances() -> None:
    with pytest.raises(LookupError, match="More than one"):
        inspect_instance_health(
            "web01", FakeEc2([instance(instance_id="i-1"), instance(instance_id="i-2")])
        )


def test_missing_region() -> None:
    with pytest.raises(RuntimeError, match="Region is not configured"):
        inspect_instance_health("web01", FakeEc2([instance()], region=None))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (NoCredentialsError(), "credentials are unavailable"),
        (
            ClientError(
                {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}},
                "DescribeInstances",
            ),
            r"AWS rejected.*UnauthorizedOperation.*denied",
        ),
    ],
)
def test_missing_credentials_or_aws_client_error(
    error: Exception, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        inspect_instance_health("web01", FakeEc2([instance()], error=error))


def test_instance_allowlist_rejection_happens_before_aws_call() -> None:
    client = FakeEc2([instance()])

    with pytest.raises(ValueError, match="not approved"):
        inspect_instance_health("database01", client)

    assert client.describe_instances_request is None
