"""Kubernetes MCP Server — exposes K8s diagnostic tools via Model Context Protocol.

Per tool-design.md §7, three tools are provided:
- get_pod:   get Pod current status
- get_events: get Kubernetes Events for a resource
- get_logs:   get container stdout/stderr logs

Each tool returns a structured dict with result/status and data.
Evidence conversion happens in the Evidence Builder (Tool Layer), not here.
"""

import json
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

import os

from infrastructure_agent.adapters.k8s_client import KubernetesClient

server = Server("kubernetes-mcp-server")
_k8s = KubernetesClient(mode=os.getenv("K8S_MODE", "mock"))


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_pod",
            description="Get a Kubernetes Pod's current status, restart count, container states, exit code, and reason.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster": {"type": "string", "description": "Cluster name, e.g. prod"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "pod": {"type": "string", "description": "Pod name"},
                },
                "required": ["cluster", "namespace", "pod"],
            },
        ),
        Tool(
            name="get_events",
            description="Get Kubernetes Events for a specific resource (e.g. pod/nginx). Focus on Warning-type events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster": {"type": "string", "description": "Cluster name, e.g. prod"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "resource": {
                        "type": "string",
                        "description": "Resource reference, e.g. pod/nginx",
                    },
                },
                "required": ["cluster", "namespace", "resource"],
            },
        ),
        Tool(
            name="get_logs",
            description="Get stdout/stderr logs from a Pod container (container-level real-time logs). For cluster-wide log search, use Loki query_logs instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cluster": {"type": "string", "description": "Cluster name, e.g. prod"},
                    "namespace": {"type": "string", "description": "Kubernetes namespace"},
                    "pod": {"type": "string", "description": "Pod name"},
                    "container": {"type": "string", "description": "Container name"},
                    "tail": {
                        "type": "integer",
                        "description": "Number of recent log lines to return (default 200)",
                    },
                },
                "required": ["cluster", "namespace", "pod", "container"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route tool calls to the appropriate handler."""
    handlers = {
        "get_pod": _handle_get_pod,
        "get_events": _handle_get_events,
        "get_logs": _handle_get_logs,
    }

    handler = handlers.get(name)
    if handler is None:
        return [TextContent(type="text", text=json.dumps({"result": "error", "error_type": "UnknownTool", "message": f"Unknown tool: {name}"}))]

    try:
        result = await handler(arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"result": "error", "error_type": type(exc).__name__, "message": str(exc)}))]


async def _handle_get_pod(args: dict) -> dict:
    return await _k8s.get_pod(
        cluster=args["cluster"],
        namespace=args["namespace"],
        pod=args["pod"],
    )


async def _handle_get_events(args: dict) -> dict:
    return await _k8s.get_events(
        cluster=args["cluster"],
        namespace=args["namespace"],
        resource=args["resource"],
    )


async def _handle_get_logs(args: dict) -> dict:
    return await _k8s.get_logs(
        cluster=args["cluster"],
        namespace=args["namespace"],
        pod=args["pod"],
        container=args["container"],
        tail=args.get("tail", 200),
    )


async def main():
    """Entry point for running the MCP server via stdio."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
