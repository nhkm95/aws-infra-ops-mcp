from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError
import pytest

from aws_infra_ops_mcp import runtime_identity

ACCOUNT = "004401752458"
ROLE = "aws-infra-ops-mcp-lab-runtime"
ARN = f"arn:aws:sts::{ACCOUNT}:assumed-role/{ROLE}/test-session"
NOW = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)


class FakeSts:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else identity()
        self.error = error
        self.calls = 0

    def get_caller_identity(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def identity(*, account: str = ACCOUNT, arn: str = ARN) -> dict[str, str]:
    return {"Account": account, "Arn": arn, "UserId": "role-id:test-session"}


def environment(**overrides: str) -> dict[str, str]:
    values = {
        runtime_identity.ACCOUNT_ENV: ACCOUNT,
        runtime_identity.ROLE_ENV: ROLE,
    }
    values.update(overrides)
    return values


@pytest.fixture(autouse=True)
def clear_identity_cache() -> None:
    runtime_identity.reset_runtime_identity_cache()
    yield
    runtime_identity.reset_runtime_identity_cache()


def validate(sts: FakeSts, environ: dict[str, str] | None = None) -> dict[str, str]:
    return runtime_identity.validate_runtime_identity(
        sts,
        environ=environment() if environ is None else environ,
        clock=lambda: NOW,
    )


def test_correct_account_and_role_return_only_safe_identity_fields() -> None:
    result = validate(FakeSts())

    assert result == {
        "account_id": ACCOUNT,
        "role_name": ROLE,
        "session_name": "test-session",
        "arn": ARN,
        "validated_at": "2026-07-31T08:00:00+00:00",
    }


def test_wrong_account_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="account is not approved"):
        validate(FakeSts(identity(account="111122223333")))


@pytest.mark.parametrize(
    "role",
    ["some-other-runtime", "AWSReservedSSO_AdministratorAccess_f60c5b834838f5c2"],
)
def test_wrong_or_administrator_role_is_rejected(role: str) -> None:
    arn = f"arn:aws:sts::{ACCOUNT}:assumed-role/{role}/admin-session"
    with pytest.raises(RuntimeError, match="role is not approved"):
        validate(FakeSts(identity(arn=arn)))


@pytest.mark.parametrize(
    "arn",
    [
        f"arn:aws:iam::{ACCOUNT}:user/operator",
        f"arn:aws:iam::{ACCOUNT}:root",
        "not-an-arn",
        f"arn:aws:sts::{ACCOUNT}:assumed-role/{ROLE}",
    ],
)
def test_non_assumed_role_and_malformed_arns_are_rejected(arn: str) -> None:
    with pytest.raises(RuntimeError, match="not an approved assumed role"):
        validate(FakeSts(identity(arn=arn)))


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {runtime_identity.ACCOUNT_ENV: ACCOUNT},
        {runtime_identity.ROLE_ENV: ROLE},
        environment(MCP_EXPECTED_AWS_ACCOUNT_ID="123"),
        environment(MCP_EXPECTED_AWS_ROLE_NAME="role/with/path"),
        environment(MCP_EXPECTED_AWS_ROLE_NAME=" role-with-spaces "),
    ],
)
def test_missing_or_malformed_environment_is_rejected_without_sts_call(
    environ: dict[str, str],
) -> None:
    sts = FakeSts()
    with pytest.raises(RuntimeError, match="requires|malformed"):
        validate(sts, environ)
    assert sts.calls == 0


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"Arn": ARN, "UserId": "id:session"},
        {"Account": ACCOUNT, "UserId": "id:session"},
        {"Account": ACCOUNT, "Arn": ARN},
        {"Account": ACCOUNT, "Arn": ARN, "UserId": ""},
        None,
    ],
)
def test_missing_sts_response_fields_are_rejected(response: Any) -> None:
    sts = FakeSts()
    sts.response = response
    with pytest.raises(RuntimeError, match="incomplete runtime identity"):
        validate(sts)


def test_sts_client_error_is_controlled() -> None:
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "GetCallerIdentity",
    )
    with pytest.raises(RuntimeError, match="identity validation failed"):
        validate(FakeSts(error=error))


def test_successful_validation_is_cached_for_process_lifetime() -> None:
    sts = FakeSts()
    first = validate(sts)
    second = validate(sts, {})

    assert first == second
    assert first is not second
    assert sts.calls == 1


def test_failures_are_not_cached() -> None:
    failing = FakeSts(identity(account="111122223333"))
    with pytest.raises(RuntimeError):
        validate(failing)

    succeeding = FakeSts()
    assert validate(succeeding)["account_id"] == ACCOUNT
    assert failing.calls == 1
    assert succeeding.calls == 1


@pytest.mark.parametrize(
    ("module_name", "tool_name", "arguments", "service_factories"),
    [
        ("instance_health", "get_instance_health", ("web01",), ("create_ec2_client",)),
        (
            "instance_metrics",
            "get_instance_metrics",
            ("web01",),
            ("create_ec2_client", "create_cloudwatch_client"),
        ),
        ("recent_errors", "get_recent_errors", ("web01",), ("create_logs_client",)),
        (
            "recent_changes",
            "get_recent_changes",
            ("web01",),
            ("create_ec2_client", "create_cloudtrail_client"),
        ),
        (
            "service_status",
            "get_service_status",
            ("web01", "nginx"),
            ("create_ec2_client", "create_ssm_client"),
        ),
        (
            "service_journal",
            "get_service_journal",
            ("web01", "nginx"),
            ("create_ec2_client", "create_ssm_client"),
        ),
    ],
)
def test_aws_tools_stop_before_service_clients_when_identity_fails(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    tool_name: str,
    arguments: tuple[str, ...],
    service_factories: tuple[str, ...],
) -> None:
    module = __import__(
        f"aws_infra_ops_mcp.tools.{module_name}", fromlist=[module_name]
    )
    monkeypatch.setenv(runtime_identity.ACCOUNT_ENV, ACCOUNT)
    monkeypatch.setenv(runtime_identity.ROLE_ENV, ROLE)
    sts = FakeSts(identity(account="111122223333"))
    monkeypatch.setattr(module, "create_sts_client", lambda: sts)

    def unexpected_service_client() -> None:
        raise AssertionError("service client was created before identity validation")

    for factory in service_factories:
        monkeypatch.setattr(module, factory, unexpected_service_client)

    with pytest.raises(RuntimeError, match="account is not approved"):
        getattr(module, tool_name)(*arguments)
    assert sts.calls == 1
