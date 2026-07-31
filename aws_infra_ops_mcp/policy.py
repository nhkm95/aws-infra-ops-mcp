"""Small allowlists that define the approved lab boundary.

These application-level checks complement the AWS tag filters and IAM policy
without changing the public MCP tool schemas.
"""

ALLOWED_INSTANCES = frozenset({"web01"})
ALLOWED_SERVICES = frozenset({"nginx"})
MAX_ERROR_RESULTS = 50
MIN_LOOKBACK_MINUTES = 5
MAX_LOOKBACK_MINUTES = 1440
ALLOWED_JOURNAL_LOOKBACK_MINUTES = frozenset({5, 10, 15, 30, 60, 120})
ALLOWED_JOURNAL_RESULT_LIMITS = frozenset({10, 25, 50, 100})


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


def validate_lookback_minutes(minutes: int) -> int:
    """Enforce a bounded CloudWatch Logs Insights lookback window."""
    if not MIN_LOOKBACK_MINUTES <= minutes <= MAX_LOOKBACK_MINUTES:
        raise ValueError(
            f"minutes must be between {MIN_LOOKBACK_MINUTES} and "
            f"{MAX_LOOKBACK_MINUTES}"
        )
    return minutes


def validate_journal_lookback_minutes(minutes: int) -> int:
    """Enforce the fixed journal lookback allowlist."""
    if minutes not in ALLOWED_JOURNAL_LOOKBACK_MINUTES:
        raise ValueError(
            "minutes must be one of "
            f"{sorted(ALLOWED_JOURNAL_LOOKBACK_MINUTES)}"
        )
    return minutes


def validate_journal_maximum_results(maximum_results: int) -> int:
    """Enforce the fixed journal result-limit allowlist."""
    if maximum_results not in ALLOWED_JOURNAL_RESULT_LIMITS:
        raise ValueError(
            "maximum_results must be one of "
            f"{sorted(ALLOWED_JOURNAL_RESULT_LIMITS)}"
        )
    return maximum_results
