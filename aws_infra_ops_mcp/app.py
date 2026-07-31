"""MCP protocol layer for the local infrastructure diagnostics server."""

from mcp.server.fastmcp import FastMCP

from aws_infra_ops_mcp.tools import (
    get_instance_health as inspect_instance_health,
    get_instance_metrics as inspect_instance_metrics,
    get_recent_errors as inspect_recent_errors,
    get_service_journal as inspect_service_journal,
    get_service_status as inspect_service_status,
)

mcp = FastMCP(
    "AWS Infra Ops Lab",
    instructions=(
        "Use these read-only tools to gather evidence about the approved AWS "
        "lab instance. Instance health, fixed CloudWatch metrics, recent errors, "
        "nginx service status, and a bounded nginx journal are backed by AWS "
        "read-only APIs."
    ),
)


@mcp.tool()
def get_instance_health(instance_name: str) -> dict:
    """Check EC2 state and AWS system and instance health checks."""
    return inspect_instance_health(instance_name)


@mcp.tool()
def get_instance_metrics(instance_name: str, minutes: int = 60) -> dict:
    """Get fixed EC2 performance and status metrics from CloudWatch."""
    return inspect_instance_metrics(instance_name, minutes)


@mcp.tool()
def get_recent_errors(
    instance_name: str,
    maximum_results: int = 10,
    minutes: int = 60,
) -> dict:
    """Get recent CloudWatch application and operating-system errors."""
    return inspect_recent_errors(instance_name, maximum_results, minutes)


@mcp.tool()
def get_service_status(instance_name: str, service_name: str) -> dict:
    """Check the current state of an approved service on an instance."""
    return inspect_service_status(instance_name, service_name)


@mcp.tool()
def get_service_journal(
    instance_name: str,
    service_name: str,
    minutes: int = 60,
    maximum_results: int = 50,
) -> dict:
    """Get a bounded nginx systemd journal through a fixed SSM document."""
    return inspect_service_journal(
        instance_name,
        service_name,
        minutes,
        maximum_results,
    )


def main() -> None:
    """Run the local MCP server over standard input and output."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
