import pytest

from aws_infra_ops_mcp.tools.simulated import (
    get_recent_errors,
    get_service_status,
)


def test_get_recent_errors_applies_result_limit() -> None:
    result = get_recent_errors("web01", maximum_results=1)

    assert result["result_count"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["source"] == "nginx"


@pytest.mark.parametrize("maximum_results", [0, 51])
def test_get_recent_errors_rejects_unbounded_limits(maximum_results: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        get_recent_errors("web01", maximum_results=maximum_results)


def test_get_service_status_accepts_normalized_approved_service() -> None:
    result = get_service_status("web01", " NGINX ")

    assert result == {
        "instance_name": "web01",
        "service_name": "nginx",
        "active_state": "failed",
        "sub_state": "failed",
        "enabled_at_boot": True,
        "data_source": "simulated",
    }


@pytest.mark.parametrize(
    ("function", "arguments"),
    [
        (get_recent_errors, ("database01",)),
        (get_service_status, ("database01", "nginx")),
        (get_service_status, ("web01", "sshd")),
    ],
)
def test_tools_reject_resources_outside_allowlist(function, arguments) -> None:
    with pytest.raises(ValueError, match="not approved"):
        function(*arguments)


def test_remaining_tools_are_explicitly_simulated() -> None:
    assert get_recent_errors("web01")["data_source"] == "simulated"
    assert get_service_status("web01", "nginx")["data_source"] == "simulated"
