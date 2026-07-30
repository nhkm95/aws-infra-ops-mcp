"""Shared resolution of approved EC2 instances."""

from typing import Any

from aws_infra_ops_mcp.policy import validate_instance


def resolve_approved_instance(
    instance_name: str,
    ec2_client: Any,
) -> tuple[str, dict[str, Any]]:
    """Resolve one allowlisted, tagged, non-terminated EC2 instance."""
    normalized_name = validate_instance(instance_name)
    response = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [normalized_name]},
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
    )
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
        if instance.get("State", {}).get("Name") != "terminated"
    ]

    if not instances:
        raise LookupError(
            f"No non-terminated EC2 instance matched approved name "
            f"'{normalized_name}' and tag MCPAccess=allowed."
        )
    if len(instances) > 1:
        raise LookupError(
            f"More than one non-terminated EC2 instance matched approved name "
            f"'{normalized_name}' and tag MCPAccess=allowed."
        )

    return normalized_name, instances[0]
