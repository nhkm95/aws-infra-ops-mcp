"""Tool implementations exposed by the MCP server."""

from aws_infra_ops_mcp.tools.instance_health import get_instance_health
from aws_infra_ops_mcp.tools.simulated import get_recent_errors, get_service_status

__all__ = [
    "get_instance_health",
    "get_recent_errors",
    "get_service_status",
]
