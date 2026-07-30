"""Small allowlists that define the approved lab boundary.

These application-level checks complement the AWS tag filters and IAM policy
without changing the public MCP tool schemas.
"""

ALLOWED_INSTANCES = frozenset({"web01"})
ALLOWED_SERVICES = frozenset({"nginx"})
MAX_ERROR_RESULTS = 50


def validate_instance(instance_name: str) -> str:
    """Return a normalized, approved instance name."""
    normalized_name = instance_name.strip().lower()
    if normalized_name not in ALLOWED_INSTANCES:
        raise ValueError(
            f"Instance '{instance_name}' is not approved. "
            f"Allowed instances: {sorted(ALLOWED_INSTANCES)}"
        )
    return normalized_name


def validate_service(service_name: str) -> str:
    """Return a normalized, approved service name."""
    normalized_name = service_name.strip().lower()
    if normalized_name not in ALLOWED_SERVICES:
        raise ValueError(
            f"Service '{service_name}' is not approved. "
            f"Allowed services: {sorted(ALLOWED_SERVICES)}"
        )
    return normalized_name


def validate_maximum_results(maximum_results: int) -> int:
    """Enforce a bounded result size for log-like tool output."""
    if not 1 <= maximum_results <= MAX_ERROR_RESULTS:
        raise ValueError(
            f"maximum_results must be between 1 and {MAX_ERROR_RESULTS}"
        )
    return maximum_results
