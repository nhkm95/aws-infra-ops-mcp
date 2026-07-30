"""Simulated read-only service and error diagnostics."""

from typing import Any

from aws_infra_ops_mcp.policy import (
    validate_instance,
    validate_maximum_results,
    validate_service,
)

def get_recent_errors(
    instance_name: str,
    maximum_results: int = 10,
) -> dict[str, Any]:
    """Return recent simulated errors for an approved lab instance."""
    normalized_instance = validate_instance(instance_name)
    validated_limit = validate_maximum_results(maximum_results)

    errors = [
        {
            "timestamp": "2026-07-30T02:20:15Z",
            "source": "nginx",
            "severity": "error",
            "message": (
                "SSL_CTX_use_PrivateKey_file failed: "
                "private key file could not be opened"
            ),
        },
        {
            "timestamp": "2026-07-30T02:20:15Z",
            "source": "systemd",
            "severity": "error",
            "message": "Failed to start nginx.service.",
        },
    ]
    selected_errors = errors[:validated_limit]

    return {
        "instance_name": normalized_instance,
        "result_count": len(selected_errors),
        "errors": selected_errors,
        "data_source": "simulated",
    }


def get_service_status(
    instance_name: str,
    service_name: str,
) -> dict[str, Any]:
    """Return the simulated state of an approved service on a lab instance."""
    normalized_instance = validate_instance(instance_name)
    normalized_service = validate_service(service_name)

    return {
        "instance_name": normalized_instance,
        "service_name": normalized_service,
        "active_state": "failed",
        "sub_state": "failed",
        "enabled_at_boot": True,
        "data_source": "simulated",
    }
