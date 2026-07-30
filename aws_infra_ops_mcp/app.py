"""MCP protocol layer for the local infrastructure diagnostics server."""

from mcp.server.fastmcp import FastMCP

from aws_infra_ops_mcp.tools import (
    get_instance_health as inspect_instance_health,
    get_recent_errors as inspect_recent_errors,
    get_service_status as inspect_service_status,
)

mcp = FastMCP(
    "AWS Infra Ops Lab",
    instructions=(
        "Use these read-only tools to gather evidence about the approved AWS "
        "lab instance. Instance health is AWS-backed; service status and recent "
        "errors are simulated and must not be described as live AWS evidence."
    ),
)


@mcp.tool()
def get_instance_health(instance_name: str) -> dict:
    """Check EC2 state and AWS system and instance health checks."""
    return inspect_instance_health(instance_name)


@mcp.tool()
def get_recent_errors(
    instance_name: str,
    maximum_results: int = 10,
) -> dict:
    """Get recent application and operating-system errors for an instance."""
    return inspect_recent_errors(instance_name, maximum_results)


@mcp.tool()
def get_service_status(instance_name: str, service_name: str) -> dict:
    """Check the current state of an approved service on an instance."""
    return inspect_service_status(instance_name, service_name)


def main() -> None:
    """Run the local MCP server over standard input and output."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
