from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError

import aws_infra_ops_mcp.tools.instance_metrics as metrics_module
from aws_infra_ops_mcp.tools.instance_metrics import (
    PERIOD_SECONDS,
    get_instance_metrics,
    inspect_instance_metrics,
)

NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)
QUERY_IDS = (
    "cpu_average",
    "cpu_maximum",
    "status_maximum",
    "status_instance_maximum",
    "status_system_maximum",
    "network_in_sum",
    "network_out_sum",
)


class FakeEc2:
    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.instances = instances
        self.request: dict[str, Any] | None = None

    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {"Reservations": [{"Instances": self.instances}]}


class FakeCloudWatch:
    def __init__(
        self,
        results: list[dict[str, Any]],
        *,
        error: Exception | None = None,
        region: str = "ap-southeast-1",
    ) -> None:
        self.meta = SimpleNamespace(region_name=region)
        self.results = results
        self.error = error
        self.request: dict[str, Any] | None = None

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        if self.error:
            raise self.error
        return {"MetricDataResults": self.results}


def instance(instance_id: str = "i-123") -> dict[str, Any]:
    return {"InstanceId": instance_id, "State": {"Name": "running"}}


def complete_results(
    values: dict[str, list[int | float]] | None = None,
) -> list[dict[str, Any]]:
    selected = values or {}
    return [
        {
            "Id": query_id,
            "StatusCode": "Complete",
            "Values": selected.get(query_id, [0]),
        }
        for query_id in QUERY_IDS
    ]


def inspect(
    cloudwatch: FakeCloudWatch,
    minutes: int = 60,
    *,
    ec2: FakeEc2 | None = None,
) -> dict[str, Any]:
    return inspect_instance_metrics(
        "web01",
        minutes,
        ec2 or FakeEc2([instance()]),
        cloudwatch,
        clock=lambda: NOW,
    )


def test_successful_metric_response_and_exact_data_shaping() -> None:
    ec2 = FakeEc2([instance("i-resolved")])
    cloudwatch = FakeCloudWatch(
        complete_results(
            {
                "cpu_average": [8.35, 8.45],
                "cpu_maximum": [17.123, 22.11],
                "status_maximum": [0, 1],
                "status_instance_maximum": [0, 0],
                "status_system_maximum": [0, 0],
                "network_in_sum": [123000, 456],
                "network_out_sum": [654000, 321],
            }
        )
    )

    result = inspect(cloudwatch, ec2=ec2)

    assert result == {
        "instance_name": "web01",
        "instance_id": "i-resolved",
        "region": "ap-southeast-1",
        "lookback_minutes": 60,
        "period_seconds": 300,
        "metrics": {
            "cpu_utilization": {
                "average": 8.4,
                "maximum": 22.11,
                "unit": "Percent",
            },
            "status_check_failed": {"maximum": 1, "unit": "Count"},
            "status_check_failed_instance": {"maximum": 0, "unit": "Count"},
            "status_check_failed_system": {"maximum": 0, "unit": "Count"},
            "network_in": {"total": 123456, "unit": "Bytes"},
            "network_out": {"total": 654321, "unit": "Bytes"},
        },
        "data_source": "aws-cloudwatch-metrics",
        "checked_at": "2026-07-30T03:00:00+00:00",
    }
    assert ec2.request == {
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
    assert cloudwatch.request is not None
    assert cloudwatch.request["StartTime"] == NOW - timedelta(minutes=60)
    assert cloudwatch.request["EndTime"] == NOW
    assert cloudwatch.request["ScanBy"] == "TimestampDescending"
    queries = cloudwatch.request["MetricDataQueries"]
    assert len(queries) == 7
    assert {query["MetricStat"]["Metric"]["Namespace"] for query in queries} == {
        "AWS/EC2"
    }
    assert {query["MetricStat"]["Period"] for query in queries} == {PERIOD_SECONDS}
    assert {
        (
            query["MetricStat"]["Metric"]["MetricName"],
            query["MetricStat"]["Stat"],
            query["MetricStat"]["Unit"],
        )
        for query in queries
    } == {
        ("CPUUtilization", "Average", "Percent"),
        ("CPUUtilization", "Maximum", "Percent"),
        ("StatusCheckFailed", "Maximum", "Count"),
        ("StatusCheckFailed_Instance", "Maximum", "Count"),
        ("StatusCheckFailed_System", "Maximum", "Count"),
        ("NetworkIn", "Sum", "Bytes"),
        ("NetworkOut", "Sum", "Bytes"),
    }
    assert {
        tuple(
            (dimension["Name"], dimension["Value"])
            for dimension in query["MetricStat"]["Metric"]["Dimensions"]
        )
        for query in queries
    } == {(("InstanceId", "i-resolved"),)}


@pytest.mark.parametrize("minutes", [5, 1440])
def test_minimum_and_maximum_valid_lookbacks(minutes: int) -> None:
    cloudwatch = FakeCloudWatch(complete_results())
    assert inspect(cloudwatch, minutes)["lookback_minutes"] == minutes


@pytest.mark.parametrize("minutes", [4, 1441])
def test_rejects_out_of_bounds_before_aws_calls(minutes: int) -> None:
    ec2 = FakeEc2([instance()])
    cloudwatch = FakeCloudWatch(complete_results())
    with pytest.raises(ValueError, match="between 5 and 1440"):
        inspect_instance_metrics("web01", minutes, ec2, cloudwatch)
    assert ec2.request is None
    assert cloudwatch.request is None


def test_empty_cloudwatch_datapoints_are_null_not_zero() -> None:
    result = inspect(FakeCloudWatch(complete_results({key: [] for key in QUERY_IDS})))
    assert result["metrics"] == {
        "cpu_utilization": {"average": None, "maximum": None, "unit": "Percent"},
        "status_check_failed": {"maximum": None, "unit": "Count"},
        "status_check_failed_instance": {"maximum": None, "unit": "Count"},
        "status_check_failed_system": {"maximum": None, "unit": "Count"},
        "network_in": {"total": None, "unit": "Bytes"},
        "network_out": {"total": None, "unit": "Bytes"},
    }


def test_partial_metric_datapoints_preserve_available_values() -> None:
    result = inspect(
        FakeCloudWatch(
            complete_results({"cpu_average": [], "network_out_sum": [10, 20]})
        )
    )
    assert result["metrics"]["cpu_utilization"]["average"] is None
    assert result["metrics"]["network_out"]["total"] == 30


def test_incomplete_cloudwatch_response_is_controlled() -> None:
    with pytest.raises(RuntimeError, match="incomplete metric response"):
        inspect(FakeCloudWatch(complete_results()[:-1]))


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("AccessDeniedException", "AccessDeniedException"),
        ("Throttling", "Throttling"),
    ],
)
def test_cloudwatch_client_errors_are_controlled(code: str, message: str) -> None:
    error = ClientError(
        {"Error": {"Code": code, "Message": "request rejected"}},
        "GetMetricData",
    )
    with pytest.raises(RuntimeError, match=message):
        inspect(FakeCloudWatch([], error=error))


def test_missing_approved_instance_is_controlled_without_metrics_call() -> None:
    cloudwatch = FakeCloudWatch(complete_results())
    with pytest.raises(LookupError, match="approved name"):
        inspect(cloudwatch, ec2=FakeEc2([]))
    assert cloudwatch.request is None


def test_unapproved_instance_is_rejected_before_aws_calls() -> None:
    ec2 = FakeEc2([instance()])
    cloudwatch = FakeCloudWatch(complete_results())
    with pytest.raises(ValueError, match="not approved"):
        inspect_instance_metrics("database01", 60, ec2, cloudwatch)
    assert ec2.request is None
    assert cloudwatch.request is None


def test_default_lookback_and_client_factories_are_injectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ec2 = FakeEc2([instance()])
    cloudwatch = FakeCloudWatch(complete_results())
    monkeypatch.setattr(metrics_module, "create_ec2_client", lambda: ec2)
    monkeypatch.setattr(
        metrics_module, "create_cloudwatch_client", lambda: cloudwatch
    )

    assert get_instance_metrics("web01")["lookback_minutes"] == 60
    assert cloudwatch.request is not None


def test_tests_use_fakes_instead_of_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_live_call(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a live AWS client was created")

    monkeypatch.setattr(metrics_module, "create_ec2_client", fail_live_call)
    monkeypatch.setattr(metrics_module, "create_cloudwatch_client", fail_live_call)

    result = inspect(FakeCloudWatch(complete_results()))
    assert result["instance_id"] == "i-123"
