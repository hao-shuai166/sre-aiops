"""MCP Server — Model Context Protocol server implementations.

Kubernetes MCP Server exposes K8s diagnostic tools (get_pod/get_events/get_logs)
via the MCP protocol. Workflow / LangGraph connects to these tools via MCP Client /
LangGraph ToolNode, not by hardcoding tool references.
"""

from infrastructure_agent.mcp.kubernetes_server import call_tool, list_tools, server

__all__ = ["call_tool", "list_tools", "server"]
