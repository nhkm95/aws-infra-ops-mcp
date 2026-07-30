"""Read-only nginx status evidence retrieved through AWS Systems Manager."""

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import create_ec2_client, create_ssm_client
from aws_infra_ops_mcp.policy import validate_instance, validate_service
from aws_infra_ops_mcp.tools.instance_resolution import resolve_approved_instance

DEFAULT_SSM_DOCUMENT_NAME = "mcp-lab-get-nginx-status"
LOCAL_POLL_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.2
MAX_OUTPUT_CHARS = 4096
MAX_DIAGNOSTIC_CHARS = 512
SUCCESS_STATUS = "Success"
TERMINAL_FAILURE_STATUSES = frozenset(
    {
        "Cancelled",
        "Cancelling",
        "Failed",
        "TimedOut",
        "Undeliverable",
        "Terminated",
        "Delivery Timed Out",
        "Execution Timed Out",
    }
)

MonotonicClock = Callable[[], float]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _bounded_diagnostic(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_DIAGNOSTIC_CHARS]


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


def _is_invocation_not_ready(error: ClientError) -> bool:
    return (
        error.response.get("Error", {}).get("Code")
        == "InvocationDoesNotExist"
    )


def _parse_status_output(output: Any) -> dict[str, Any]:
    if not isinstance(output, str):
        raise RuntimeError("SSM returned service-status output with an invalid type.")
    if len(output) > MAX_OUTPUT_CHARS:
        raise RuntimeError("SSM returned unexpectedly large service-status output.")

    stripped = output.strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        raise RuntimeError("SSM returned malformed service-status JSON.")
    try:
        evidence = json.loads(stripped)
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("SSM returned malformed service-status JSON.") from error

    expected_fields = {
        "service_name",
        "active_state",
        "sub_state",
        "enabled_at_boot",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_fields:
        raise RuntimeError("SSM returned service-status JSON with unexpected fields.")
    if evidence["service_name"] != "nginx":
        raise RuntimeError("SSM returned service-status JSON for an unexpected service.")
    if not isinstance(evidence["active_state"], str) or not evidence["active_state"]:
        raise RuntimeError("SSM returned an invalid active_state.")
    if not isinstance(evidence["sub_state"], str) or not evidence["sub_state"]:
        raise RuntimeError("SSM returned an invalid sub_state.")
    if type(evidence["enabled_at_boot"]) is not bool:
        raise RuntimeError("SSM returned an invalid enabled_at_boot value.")
    return evidence


def inspect_service_status(
    instance_name: str,
    service_name: str,
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
    """Resolve an approved instance and run the fixed nginx status document."""
    normalized_instance = validate_instance(instance_name)
    normalized_service = validate_service(service_name)
    configured_document = (
        document_name
        if document_name is not None
        else os.environ.get("AWS_SSM_DOCUMENT_NAME", DEFAULT_SSM_DOCUMENT_NAME)
    )
    configured_document = configured_document.strip() or DEFAULT_SSM_DOCUMENT_NAME

    try:
        normalized_instance, instance = resolve_approved_instance(
            normalized_instance, ec2_client
        )
        instance_id = instance["InstanceId"]
        started = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName=configured_document,
            TimeoutSeconds=30,
            MaxConcurrency="1",
            MaxErrors="0",
            Comment="Read-only nginx status check for MCP diagnostics",
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
                    evidence = _parse_status_output(
                        invocation.get("StandardOutputContent")
                    )
                    break
                if status in TERMINAL_FAILURE_STATUSES:
                    diagnostic = _bounded_diagnostic(
                        invocation.get("StandardErrorContent")
                        or invocation.get("StatusDetails")
                        or status
                    )
                    raise RuntimeError(
                        f"SSM service-status command ended with status {status}: "
                        f"{diagnostic}"
                    )

            now = monotonic()
            if now >= deadline:
                raise RuntimeError(
                    "SSM service-status command exceeded the local polling timeout."
                )
            sleep(min(polling_interval, max(0.0, deadline - now)))
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("EC2 or SSM", error)) from error

    return {
        "instance_name": normalized_instance,
        "instance_id": instance_id,
        **evidence,
        "data_source": "aws-ssm",
        "checked_at": clock().astimezone(timezone.utc).isoformat(),
    }


def get_service_status(instance_name: str, service_name: str) -> dict[str, Any]:
    """Return live nginx status for an approved EC2 instance."""
    normalized_instance = validate_instance(instance_name)
    normalized_service = validate_service(service_name)
    try:
        ec2_client = create_ec2_client()
        ssm_client = create_ssm_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message("client creation", error)) from error
    return inspect_service_status(
        normalized_instance,
        normalized_service,
        ec2_client,
        ssm_client,
    )
