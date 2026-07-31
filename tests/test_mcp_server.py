import asyncio

from aws_infra_ops_mcp.app import mcp


def test_server_exposes_only_the_expected_read_only_tools() -> None:
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools} == {
        "get_instance_health",
        "get_instance_metrics",
        "get_recent_errors",
        "get_service_journal",
        "get_service_status",
    }


def test_tool_schemas_include_expected_inputs() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    health_schema = tools["get_instance_health"].inputSchema
    metrics_schema = tools["get_instance_metrics"].inputSchema
    error_schema = tools["get_recent_errors"].inputSchema
    journal_schema = tools["get_service_journal"].inputSchema
    service_schema = tools["get_service_status"].inputSchema

    assert health_schema["required"] == ["instance_name"]
    assert metrics_schema["properties"]["minutes"]["default"] == 60
    assert metrics_schema["required"] == ["instance_name"]
    assert error_schema["properties"]["maximum_results"]["default"] == 10
    assert error_schema["properties"]["minutes"]["default"] == 60
    assert error_schema["required"] == ["instance_name"]
    assert journal_schema["properties"]["minutes"]["default"] == 60
    assert journal_schema["properties"]["maximum_results"]["default"] == 50
    assert set(journal_schema["required"]) == {"instance_name", "service_name"}
    assert set(service_schema["required"]) == {"instance_name", "service_name"}
