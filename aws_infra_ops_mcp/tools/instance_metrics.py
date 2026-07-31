"""Read-only, fixed CloudWatch metrics for an approved EC2 instance."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import (
    create_cloudwatch_client,
    create_ec2_client,
    create_sts_client,
)
from aws_infra_ops_mcp.policy import validate_instance, validate_lookback_minutes
from aws_infra_ops_mcp.runtime_identity import validate_runtime_identity
from aws_infra_ops_mcp.tools.instance_resolution import resolve_approved_instance

PERIOD_SECONDS = 300
Clock = Callable[[], datetime]

_METRICS = (
    ("cpu_average", "CPUUtilization", "Average", "Percent"),
    ("cpu_maximum", "CPUUtilization", "Maximum", "Percent"),
    ("status_maximum", "StatusCheckFailed", "Maximum", "Count"),
    ("status_instance_maximum", "StatusCheckFailed_Instance", "Maximum", "Count"),
    ("status_system_maximum", "StatusCheckFailed_System", "Maximum", "Count"),
    ("network_in_sum", "NetworkIn", "Sum", "Bytes"),
    ("network_out_sum", "NetworkOut", "Sum", "Bytes"),
)


def _bounded_diagnostic(value: Any) -> str:
    return " ".join(str(value or "").split())[:512]


def _aws_error_message(service: str, error: Exception) -> str:
    if isinstance(error, NoRegionError):
        return "AWS Region is not configured; configure it through the standard AWS chain."
    if isinstance(error, NoCredentialsError):
        return "AWS credentials are unavailable from the standard AWS credential chain."
    if isinstance(error, ClientError):
        details = error.response.get("Error", {})
        code = _bounded_diagnostic(details.get("Code", "unknown"))
        message = _bounded_diagnostic(details.get("Message", "request rejected"))
        return f"AWS rejected the {service} request ({code}): {message}"
    return f"The AWS {service} request failed: {_bounded_diagnostic(error)}"


def _queries(instance_id: str) -> list[dict[str, Any]]:
    return [
        {
            "Id": query_id,
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/EC2",
                    "MetricName": metric_name,
                    "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                },
                "Period": PERIOD_SECONDS,
                "Stat": statistic,
                "Unit": unit,
            },
            "ReturnData": True,
        }
        for query_id, metric_name, statistic, unit in _METRICS
    ]


def _round(value: float) -> int | float:
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def _aggregate(results: list[dict[str, Any]]) -> dict[str, int | float | None]:
    expected_ids = {query_id for query_id, *_ in _METRICS}
    by_id: dict[str, dict[str, Any]] = {}
    for result in results:
        query_id = result.get("Id")
        if query_id in expected_ids:
            if query_id in by_id:
                raise RuntimeError("CloudWatch returned duplicate metric results.")
            by_id[query_id] = result

    if set(by_id) != expected_ids:
        raise RuntimeError("CloudWatch returned an incomplete metric response.")
    if any(result.get("StatusCode") != "Complete" for result in by_id.values()):
        raise RuntimeError("CloudWatch returned an incomplete metric response.")

    values: dict[str, int | float | None] = {}
    for query_id in expected_ids:
        raw_values = by_id[query_id].get("Values")
        if not isinstance(raw_values, list):
            raise RuntimeError("CloudWatch returned a malformed metric response.")
        if any(type(value) not in (int, float) for value in raw_values):
            raise RuntimeError("CloudWatch returned a malformed metric response.")
        if not raw_values:
            values[query_id] = None
        elif query_id == "cpu_average":
            values[query_id] = _round(sum(raw_values) / len(raw_values))
        elif query_id in {"network_in_sum", "network_out_sum"}:
            values[query_id] = _round(sum(raw_values))
        else:
            values[query_id] = _round(max(raw_values))
    return values


def inspect_instance_metrics(
    instance_name: str,
    minutes: int,
    ec2_client: Any,
    cloudwatch_client: Any,
    *,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Resolve an approved instance and retrieve the fixed metric allowlist."""
    normalized_name = validate_instance(instance_name)
    validated_minutes = validate_lookback_minutes(minutes)
    region = getattr(getattr(cloudwatch_client, "meta", None), "region_name", None)
    if not region:
        raise RuntimeError(
            "AWS Region is not configured; configure it through the standard AWS chain."
        )

    checked_at = clock().astimezone(timezone.utc)
    try:
        normalized_name, instance = resolve_approved_instance(
            normalized_name, ec2_client
        )
        instance_id = instance.get("InstanceId")
        if not isinstance(instance_id, str) or not instance_id:
            raise RuntimeError("EC2 returned an incomplete instance response.")
        response = cloudwatch_client.get_metric_data(
            MetricDataQueries=_queries(instance_id),
            StartTime=checked_at - timedelta(minutes=validated_minutes),
            EndTime=checked_at,
            ScanBy="TimestampDescending",
        )
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        service = (
            "EC2"
            if isinstance(error, ClientError)
            and error.operation_name == "DescribeInstances"
            else "CloudWatch"
        )
        raise RuntimeError(_aws_error_message(service, error)) from error

    if not isinstance(response, dict):
        raise RuntimeError("CloudWatch returned a malformed metric response.")
    if response.get("NextToken"):
        raise RuntimeError("CloudWatch returned an incomplete metric response.")
    results = response.get("MetricDataResults")
    if not isinstance(results, list):
        raise RuntimeError("CloudWatch returned a malformed metric response.")
    values = _aggregate(results)
    return {
        "instance_name": normalized_name,
        "instance_id": instance_id,
        "region": region,
        "lookback_minutes": validated_minutes,
        "period_seconds": PERIOD_SECONDS,
        "metrics": {
            "cpu_utilization": {
                "average": values["cpu_average"],
                "maximum": values["cpu_maximum"],
                "unit": "Percent",
            },
            "status_check_failed": {
                "maximum": values["status_maximum"],
                "unit": "Count",
            },
            "status_check_failed_instance": {
                "maximum": values["status_instance_maximum"],
                "unit": "Count",
            },
            "status_check_failed_system": {
                "maximum": values["status_system_maximum"],
                "unit": "Count",
            },
            "network_in": {"total": values["network_in_sum"], "unit": "Bytes"},
            "network_out": {"total": values["network_out_sum"], "unit": "Bytes"},
        },
        "data_source": "aws-cloudwatch-metrics",
        "checked_at": checked_at.isoformat(),
    }


def get_instance_metrics(instance_name: str, minutes: int = 60) -> dict[str, Any]:
    """Return live, read-only fixed metrics for an approved instance."""
    normalized_name = validate_instance(instance_name)
    validated_minutes = validate_lookback_minutes(minutes)
    try:
        validate_runtime_identity(create_sts_client())
        ec2_client = create_ec2_client()
        cloudwatch_client = create_cloudwatch_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("AWS client creation", error)) from error
    return inspect_instance_metrics(
        normalized_name,
        validated_minutes,
        ec2_client,
        cloudwatch_client,
    )
