"""Fail-closed validation of the AWS principal used by the MCP runtime."""

from collections.abc import Mapping
from datetime import datetime, timezone
import os
import re
from threading import Lock
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

ACCOUNT_ENV = "MCP_EXPECTED_AWS_ACCOUNT_ID"
ROLE_ENV = "MCP_EXPECTED_AWS_ROLE_NAME"

_ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
_ROLE_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_-]{1,64}$")
_ASSUMED_ROLE_ARN_PATTERN = re.compile(
    r"^arn:aws:sts::(?P<account>[0-9]{12}):assumed-role/"
    r"(?P<role>[A-Za-z0-9+=,.@_-]{1,64})/"
    r"(?P<session>[A-Za-z0-9+=,.@_-]{1,64})$"
)

Clock = Callable[[], datetime]

_validation_lock = Lock()
_cached_identity: dict[str, str] | None = None


def _required_setting(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Runtime identity validation requires {name}.")
    if value != value.strip():
        raise RuntimeError(f"Runtime identity setting {name} is malformed.")
    return value


def _configuration(environ: Mapping[str, str]) -> tuple[str, str]:
    account_id = _required_setting(environ, ACCOUNT_ENV)
    role_name = _required_setting(environ, ROLE_ENV)
    if not _ACCOUNT_PATTERN.fullmatch(account_id):
        raise RuntimeError(f"Runtime identity setting {ACCOUNT_ENV} is malformed.")
    if not _ROLE_PATTERN.fullmatch(role_name):
        raise RuntimeError(f"Runtime identity setting {ROLE_ENV} is malformed.")
    return account_id, role_name


def validate_runtime_identity(
    sts_client: Any,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> dict[str, str]:
    """Validate and cache only an approved STS assumed-role identity."""
    global _cached_identity

    with _validation_lock:
        if _cached_identity is not None:
            return dict(_cached_identity)

        expected_account, expected_role = _configuration(
            os.environ if environ is None else environ
        )
        try:
            response = sts_client.get_caller_identity()
        except (ClientError, BotoCoreError) as error:
            raise RuntimeError("AWS runtime identity validation failed.") from error

        if not isinstance(response, dict):
            raise RuntimeError("STS returned an incomplete runtime identity.")
        account = response.get("Account")
        arn = response.get("Arn")
        user_id = response.get("UserId")
        if not all(isinstance(value, str) and value for value in (account, arn, user_id)):
            raise RuntimeError("STS returned an incomplete runtime identity.")
        if account != expected_account:
            raise RuntimeError("AWS runtime account is not approved.")

        match = _ASSUMED_ROLE_ARN_PATTERN.fullmatch(arn)
        if match is None:
            raise RuntimeError("AWS runtime identity is not an approved assumed role.")
        if match.group("account") != expected_account:
            raise RuntimeError("AWS runtime ARN account is not approved.")
        if match.group("role") != expected_role:
            raise RuntimeError("AWS runtime role is not approved.")

        result = {
            "account_id": account,
            "role_name": match.group("role"),
            "session_name": match.group("session"),
            "arn": arn,
            "validated_at": clock().astimezone(timezone.utc).isoformat(),
        }
        _cached_identity = result
        return dict(result)


def reset_runtime_identity_cache() -> None:
    """Clear process state for isolated tests; production code never calls this."""
    global _cached_identity
    with _validation_lock:
        _cached_identity = None
