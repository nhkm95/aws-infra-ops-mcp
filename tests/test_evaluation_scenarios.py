import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from aws_infra_ops_mcp.app import mcp

EVALUATIONS_DIR = Path(__file__).parents[1] / "evaluations"
SCENARIO_PATHS = sorted(EVALUATIONS_DIR.glob("*.json"))
REQUIRED_FIELDS = {
    "id",
    "title",
    "purpose",
    "user_question",
    "expected_tools",
    "required_findings",
    "acceptable_conclusions",
    "prohibited_claims",
    "evidence_sources",
    "manual_setup",
    "manual_recovery",
    "safety_notes",
}
SENSITIVE_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ASIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:aws_secret_access_key|aws_session_token|password)"
        r"\s*[:=]\s*[\"']?(?!<)[A-Za-z0-9/+=]{8,}"
    ),
)
UNRESTRICTED_REQUEST_TERMS = (
    "shell tool",
    "terminal tool",
    "run a shell",
    "execute a command",
    "aws cli",
    "arbitrary cloudwatch",
    "generic aws",
)


def load_scenario(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as scenario_file:
        value = json.load(scenario_file)
    assert isinstance(value, dict), f"{path.name} must contain a JSON object"
    return value


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=lambda path: path.stem)
def test_scenario_json_parses_and_has_required_fields(path: Path) -> None:
    scenario = load_scenario(path)

    assert set(scenario) >= REQUIRED_FIELDS
    assert all(
        isinstance(scenario[field], str) and scenario[field].strip()
        for field in ("id", "title", "purpose", "user_question")
    )
    for field in (
        "expected_tools",
        "required_findings",
        "acceptable_conclusions",
        "prohibited_claims",
        "evidence_sources",
        "safety_notes",
    ):
        assert isinstance(scenario[field], list)
        assert scenario[field], f"{path.name}: {field} must not be empty"


def test_scenario_ids_are_unique() -> None:
    ids = [load_scenario(path)["id"] for path in SCENARIO_PATHS]

    assert SCENARIO_PATHS, "at least one evaluation scenario is required"
    assert len(ids) == len(set(ids))


def test_expected_tools_are_currently_approved_mcp_tools() -> None:
    approved_tools = {tool.name for tool in asyncio.run(mcp.list_tools())}

    for path in SCENARIO_PATHS:
        expected_tools = load_scenario(path)["expected_tools"]
        assert all(isinstance(tool, str) for tool in expected_tools)
        assert set(expected_tools) <= approved_tools


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=lambda path: path.stem)
def test_state_changing_setup_has_manual_recovery(path: Path) -> None:
    scenario = load_scenario(path)
    setup = scenario["manual_setup"]
    recovery = scenario["manual_recovery"]

    assert isinstance(setup, dict)
    assert isinstance(setup.get("changes_state"), bool)
    assert isinstance(setup.get("steps"), list) and setup["steps"]
    assert isinstance(recovery, dict)
    assert isinstance(recovery.get("required"), bool)
    assert isinstance(recovery.get("steps"), list) and recovery["steps"]
    if setup["changes_state"]:
        assert recovery["required"] is True


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=lambda path: path.stem)
def test_scenarios_contain_no_secrets_or_unrestricted_tool_requests(
    path: Path,
) -> None:
    raw_scenario = path.read_text(encoding="utf-8")
    scenario = load_scenario(path)

    for pattern in SENSITIVE_PATTERNS:
        assert pattern.search(raw_scenario) is None

    question = scenario["user_question"].lower()
    assert not any(term in question for term in UNRESTRICTED_REQUEST_TERMS)
