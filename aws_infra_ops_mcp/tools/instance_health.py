"""Read-only EC2 instance health diagnostics."""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, NoRegionError

from aws_infra_ops_mcp.aws import create_ec2_client, create_sts_client
from aws_infra_ops_mcp.policy import validate_instance
from aws_infra_ops_mcp.runtime_identity import validate_runtime_identity
from aws_infra_ops_mcp.tools.instance_resolution import resolve_approved_instance

Clock = Callable[[], datetime]


def _aws_error_message(error: Exception) -> str:
    if isinstance(error, NoRegionError):
        return "AWS Region is not configured; configure it through the standard AWS chain."
    if isinstance(error, NoCredentialsError):
        return "AWS credentials are unavailable from the standard AWS credential chain."
    if isinstance(error, ClientError):
        details = error.response.get("Error", {})
        code = details.get("Code", "unknown")
        message = details.get("Message", "request rejected")
        return f"AWS rejected the EC2 request ({code}): {message}"
    return f"The AWS EC2 request failed: {error}"


def inspect_instance_health(
    instance_name: str,
    ec2_client: Any,
    *,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Query EC2 using an injected client and return only compact health data."""
    normalized_name = validate_instance(instance_name)
    region = getattr(getattr(ec2_client, "meta", None), "region_name", None)
    if not region:
        raise RuntimeError(
            "AWS Region is not configured; configure it through the standard AWS chain."
        )

    try:
        normalized_name, instance = resolve_approved_instance(
            normalized_name, ec2_client
        )
        instance_id = instance["InstanceId"]
        status_response = ec2_client.describe_instance_status(
            InstanceIds=[instance_id],
            IncludeAllInstances=True,
        )
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message(error)) from error

    statuses = status_response.get("InstanceStatuses", [])
    status = statuses[0] if statuses else {}
    placement = instance.get("Placement", {})

    return {
        "instance_name": normalized_name,
        "instance_id": instance_id,
        "region": region,
        "state": instance.get("State", {}).get("Name", "unknown"),
        "system_status": status.get("SystemStatus", {}).get(
            "Status", "not-applicable"
        ),
        "instance_status": status.get("InstanceStatus", {}).get(
            "Status", "not-applicable"
        ),
        "private_ip": instance.get("PrivateIpAddress"),
        "availability_zone": placement.get("AvailabilityZone"),
        "data_source": "aws",
        "checked_at": clock().astimezone(timezone.utc).isoformat(),
    }


def get_instance_health(instance_name: str) -> dict[str, Any]:
    """Return live, read-only EC2 health for an approved instance."""
    normalized_name = validate_instance(instance_name)
    try:
        validate_runtime_identity(create_sts_client())
        client = create_ec2_client()
    except (NoRegionError, NoCredentialsError, ClientError, BotoCoreError) as error:
        raise RuntimeError(_aws_error_message(error)) from error
    return inspect_instance_health(normalized_name, client)
