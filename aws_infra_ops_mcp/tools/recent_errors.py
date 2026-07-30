"""Read-only recent-error diagnostics from CloudWatch Logs Insights."""

from collections.abc import Callable
import os
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import create_logs_client
from aws_infra_ops_mcp.policy import (
    validate_instance,
    validate_lookback_minutes,
    validate_maximum_results,
)

DEFAULT_LOG_GROUP_PREFIX = "/aws/mcp-lab"
TERMINAL_ERROR_STATUSES = frozenset({"Failed", "Cancelled", "Timeout", "Unknown"})
LOCAL_QUERY_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.2
MAX_MESSAGE_CHARS = 4000
MAX_METADATA_CHARS = 512

MonotonicClock = Callable[[], float]
WallClock = Callable[[], float]
Sleeper = Callable[[float], None]


def _log_groups(instance_name: str) -> list[str]:
    prefix = os.environ.get("AWS_LOG_GROUP_PREFIX", DEFAULT_LOG_GROUP_PREFIX)
    normalized_prefix = prefix.strip().rstrip("/") or DEFAULT_LOG_GROUP_PREFIX
    return [
        f"{normalized_prefix}/{instance_name}/system",
        f"{normalized_prefix}/{instance_name}/nginx",
    ]


def _query(maximum_results: int) -> str:
    return (
        "fields @timestamp, @message, @log, @logStream\n"
        "| filter @message like /(?i)(error|failed|failure|critical|timeout|denied)/\n"
        "| sort @timestamp desc\n"
        f"| limit {maximum_results}"
    )


def _aws_error_message(error: Exception) -> str:
    if isinstance(error, NoRegionError):
        return "AWS Region is not configured; configure it through the standard AWS chain."
    if isinstance(error, NoCredentialsError):
        return "AWS credentials are unavailable from the standard AWS credential chain."
    if isinstance(error, ClientError):
        details = error.response.get("Error", {})
        return (
            "AWS rejected the CloudWatch Logs request "
            f"({details.get('Code', 'unknown')}): "
            f"{details.get('Message', 'request rejected')}"
        )
    return f"The AWS CloudWatch Logs request failed: {error}"


def _field_map(row: list[dict[str, str]]) -> dict[str, str]:
    return {
        item["field"]: item.get("value", "")
        for item in row
        if "field" in item and item["field"] in {
            "@timestamp",
            "@message",
            "@log",
            "@logStream",
        }
    }


def _approved_log_group(raw_log: str, approved_groups: list[str]) -> str:
    """Strip any account prefix from @log and return only an approved group."""
    for log_group in approved_groups:
        if raw_log == log_group or raw_log.endswith(f":{log_group}"):
            return log_group
    return ""


def _bounded(value: str, maximum_chars: int) -> str:
    """Bound individual fields as well as the number of returned rows."""
    return value[:maximum_chars]


def inspect_recent_errors(
    instance_name: str,
    maximum_results: int,
    minutes: int,
    logs_client: Any,
    *,
    monotonic: MonotonicClock = time.monotonic,
    wall_clock: WallClock = time.time,
    sleep: Sleeper = time.sleep,
    local_timeout: float = LOCAL_QUERY_TIMEOUT_SECONDS,
    polling_interval: float = POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Run the fixed Logs Insights query with an injected CloudWatch client."""
    normalized_instance = validate_instance(instance_name)
    validated_limit = validate_maximum_results(maximum_results)
    validated_minutes = validate_lookback_minutes(minutes)
    log_groups = _log_groups(normalized_instance)
    end_time = int(wall_clock())
    start_time = end_time - (validated_minutes * 60)

    try:
        started = logs_client.start_query(
            logGroupNames=log_groups,
            startTime=start_time,
            endTime=end_time,
            queryString=_query(validated_limit),
            limit=validated_limit,
        )
        query_id = started["queryId"]
        deadline = monotonic() + local_timeout

        while True:
            response = logs_client.get_query_results(queryId=query_id)
            status = response.get("status", "Unknown")
            if status == "Complete":
                rows = response.get("results", [])
                break
            if status in TERMINAL_ERROR_STATUSES:
                raise RuntimeError(
                    f"CloudWatch Logs Insights query ended with status {status}."
                )
            if monotonic() >= deadline:
                logs_client.stop_query(queryId=query_id)
                raise RuntimeError(
                    "CloudWatch Logs Insights query exceeded the local polling timeout."
                )
            sleep(min(polling_interval, max(0.0, deadline - monotonic())))
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message(error)) from error

    errors = []
    for row in rows[:validated_limit]:
        fields = _field_map(row)
        errors.append(
            {
                "timestamp": _bounded(fields.get("@timestamp", ""), MAX_METADATA_CHARS),
                "message": _bounded(fields.get("@message", ""), MAX_MESSAGE_CHARS),
                "log_group": _approved_log_group(fields.get("@log", ""), log_groups),
                "log_stream": _bounded(
                    fields.get("@logStream", ""), MAX_METADATA_CHARS
                ),
            }
        )

    return {
        "instance_name": normalized_instance,
        "lookback_minutes": validated_minutes,
        "result_count": len(errors),
        "errors": errors,
        "data_source": "aws-cloudwatch",
    }


def get_recent_errors(
    instance_name: str,
    maximum_results: int = 10,
    minutes: int = 60,
) -> dict[str, Any]:
    """Return live recent errors for an approved instance."""
    normalized_instance = validate_instance(instance_name)
    validated_limit = validate_maximum_results(maximum_results)
    validated_minutes = validate_lookback_minutes(minutes)
    try:
        client = create_logs_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message(error)) from error
    return inspect_recent_errors(
        normalized_instance,
        validated_limit,
        validated_minutes,
        client,
    )
