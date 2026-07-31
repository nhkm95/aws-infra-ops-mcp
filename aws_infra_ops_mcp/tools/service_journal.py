"""Bounded nginx systemd journal evidence retrieved through AWS Systems Manager."""

from collections.abc import Callable
from datetime import datetime, timezone
import os
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import create_ec2_client, create_ssm_client
from aws_infra_ops_mcp.policy import (
    validate_instance,
    validate_journal_lookback_minutes,
    validate_journal_maximum_results,
    validate_service,
)
from aws_infra_ops_mcp.tools.instance_resolution import resolve_approved_instance
from aws_infra_ops_mcp.tools.service_status import (
    LOCAL_POLL_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    SUCCESS_STATUS,
    TERMINAL_FAILURE_STATUSES,
    _aws_error_message,
    _bounded_diagnostic,
    _is_invocation_not_ready,
)

DEFAULT_SSM_JOURNAL_DOCUMENT_NAME = "mcp-lab-get-nginx-journal"
MAX_RAW_OUTPUT_CHARS = 48_000
MAX_LINE_CHARS = 1_000
MAX_RESPONSE_CHARS = 24_000

MonotonicClock = Callable[[], float]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _parse_journal_output(
    output: Any,
    maximum_results: int,
) -> tuple[list[str], bool]:
    if not isinstance(output, str):
        raise RuntimeError("SSM returned service-journal output with an invalid type.")
    if len(output) > MAX_RAW_OUTPUT_CHARS:
        raise RuntimeError("SSM returned unexpectedly large service-journal output.")
    if "\x00" in output:
        raise RuntimeError("SSM returned malformed service-journal output.")

    lines = output.splitlines()
    truncated = len(lines) > maximum_results
    entries: list[str] = []
    response_chars = 0
    for raw_line in lines[:maximum_results]:
        line = raw_line
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]
            truncated = True
        if response_chars + len(line) > MAX_RESPONSE_CHARS:
            truncated = True
            break
        entries.append(line)
        response_chars += len(line)
    return entries, truncated


def inspect_service_journal(
    instance_name: str,
    service_name: str,
    minutes: int,
    maximum_results: int,
    ec2_client: Any,
    ssm_client: Any,
    *,
    document_name: str | None = None,
    monotonic: MonotonicClock = time.monotonic,
    sleep: Sleeper = time.sleep,
    clock: Clock = lambda: datetime.now(timezone.utc),
    local_timeout: float = LOCAL_POLL_TIMEOUT_SECONDS,
    polling_interval: float = POLL_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Resolve an approved instance and run the fixed nginx journal document."""
    normalized_instance = validate_instance(instance_name)
    normalized_service = validate_service(service_name)
    validated_minutes = validate_journal_lookback_minutes(minutes)
    validated_maximum = validate_journal_maximum_results(maximum_results)
    configured_document = (
        document_name
        if document_name is not None
        else os.environ.get(
            "AWS_SSM_JOURNAL_DOCUMENT_NAME",
            DEFAULT_SSM_JOURNAL_DOCUMENT_NAME,
        )
    )
    configured_document = (
        configured_document.strip()
        or DEFAULT_SSM_JOURNAL_DOCUMENT_NAME
    )

    try:
        normalized_instance, instance = resolve_approved_instance(
            normalized_instance, ec2_client
        )
        instance_id = instance["InstanceId"]
        started = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName=configured_document,
            Parameters={
                "lookbackMinutes": [str(validated_minutes)],
                "maximumResults": [str(validated_maximum)],
            },
            TimeoutSeconds=30,
            MaxConcurrency="1",
            MaxErrors="0",
            Comment="Read-only bounded nginx journal for MCP diagnostics",
        )
        command_id = started["Command"]["CommandId"]
        deadline = monotonic() + local_timeout

        while True:
            try:
                invocation = ssm_client.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except ClientError as error:
                if not _is_invocation_not_ready(error):
                    raise
                invocation = None

            if invocation is not None:
                status = invocation.get("Status", "Unknown")
                if status == SUCCESS_STATUS:
                    entries, truncated = _parse_journal_output(
                        invocation.get("StandardOutputContent"),
                        validated_maximum,
                    )
                    break
                if status in TERMINAL_FAILURE_STATUSES:
                    diagnostic = _bounded_diagnostic(
                        invocation.get("StandardErrorContent")
                        or invocation.get("StatusDetails")
                        or status
                    )
                    raise RuntimeError(
                        f"SSM service-journal command ended with status {status}: "
                        f"{diagnostic}"
                    )

            now = monotonic()
            if now >= deadline:
                raise RuntimeError(
                    "SSM service-journal command exceeded the local polling timeout."
                )
            sleep(min(polling_interval, max(0.0, deadline - now)))
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("EC2 or SSM", error)) from error

    return {
        "instance_name": normalized_instance,
        "instance_id": instance_id,
        "service_name": normalized_service,
        "lookback_minutes": validated_minutes,
        "maximum_results": validated_maximum,
        "result_count": len(entries),
        "entries": entries,
        "truncated": truncated,
        "command_status": SUCCESS_STATUS,
        "data_source": "aws-ssm-journal",
        "checked_at": clock().astimezone(timezone.utc).isoformat(),
    }


def get_service_journal(
    instance_name: str,
    service_name: str,
    minutes: int = 60,
    maximum_results: int = 50,
) -> dict[str, Any]:
    """Return a bounded nginx journal for an approved EC2 instance."""
    normalized_instance = validate_instance(instance_name)
    normalized_service = validate_service(service_name)
    validated_minutes = validate_journal_lookback_minutes(minutes)
    validated_maximum = validate_journal_maximum_results(maximum_results)
    try:
        ec2_client = create_ec2_client()
        ssm_client = create_ssm_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("client creation", error)) from error
    return inspect_service_journal(
        normalized_instance,
        normalized_service,
        validated_minutes,
        validated_maximum,
        ec2_client,
        ssm_client,
    )
