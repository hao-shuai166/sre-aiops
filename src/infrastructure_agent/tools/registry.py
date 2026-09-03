"""Tool Registry — the single place where investigation tools are declared.

Each tool is a ToolSpec: name / description / JSON parameters schema / async
handler. The registry validates arguments and memoizes read-only tool calls
keyed by (tool, args), so the agent never hits the same API twice during one
investigation.

Per the 6-layer architecture, tools sit between the Agent (who decides what to
do next) and the Adapters (who fetch data). The P1 ReAct loop will call tools
exclusively through this registry.
"""

import json
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class ToolSpec:
    """Declarative definition of an investigation tool.

    Attributes:
        name: Unique tool name, e.g. ``get_pod_status``. The LLM references it
            verbatim in its action output, so keep it short and predictable.
        description: Natural-language explanation of *when* to use this tool and
            *what* it returns. This is the primary signal the LLM uses to pick
            the right tool — treat it as prompt engineering.
        parameters_schema: JSON Schema (draft-07 style) of the arguments.
        handler: Async callable receiving validated kwargs; returns the raw
            adapter response dict (``{"result": "success"|"error", ...}``).
        produces_evidence: Whether the tool output should be persisted as
            Evidence in the final report. Navigation helpers (e.g. ``list_pods``)
            are context for the LLM, not diagnosis evidence.
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        handler: Callable[..., Awaitable[dict[str, Any]]],
        produces_evidence: bool = True,
    ):
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.handler = handler
        self.produces_evidence = produces_evidence

    def to_dict(self) -> dict[str, Any]:
        """Serialize the tool for LLM-facing prompts (name/desc/schema only)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    def __repr__(self) -> str:
        return f"<ToolSpec {self.name}>"


class ToolRegistry:
    """Holds registered tools and executes them with argument validation + memoization."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_hits = 0

    # ---- Registration ----

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' already registered")
        self._tools[spec.name] = spec
        logger.debug("Registered tool: %s", spec.name)

    def register_many(self, specs: list[ToolSpec]) -> None:
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def tool_names(self) -> list[str]:
        return list(self._tools)

    # ---- Cache ----

    @staticmethod
    def _cache_key(name: str, args: dict[str, Any]) -> str:
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

    def clear_cache(self) -> None:
        self._cache.clear()
        self._cache_hits = 0

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    # ---- Execution ----

    @staticmethod
    def _filter_args(
        spec: ToolSpec, args: dict[str, Any] | None
    ) -> tuple[dict[str, Any], list[str]]:
        """Keep only declared parameters; return (clean_args, missing_required)."""
        schema = spec.parameters_schema
        props = schema.get("properties", {})
        clean = {k: v for k, v in (args or {}).items() if k in props}
        missing = [k for k in schema.get("required", []) if k not in clean]
        return clean, missing

    async def call(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """Validate and execute one tool call with memoization.

        Never raises on tool faults — returns the adapter's raw error dict so
        the agent loop can keep going. The caller (P1 execute_tool) is
        responsible for converting successful results into Evidence.
        """
        spec = self._tools.get(name)
        if spec is None:
            return {
                "result": "error",
                "error_type": "UnknownTool",
                "message": f"Unknown tool '{name}'. Available tools: {', '.join(self.tool_names())}",
            }

        clean_args, missing = self._filter_args(spec, args)
        if missing:
            return {
                "result": "error",
                "error_type": "InvalidArguments",
                "message": f"Tool '{name}' missing required arguments: {', '.join(missing)}",
            }

        key = self._cache_key(name, clean_args)
        if key in self._cache:
            self._cache_hits += 1
            return self._cache[key]

        started = time.monotonic()
        try:
            raw = await spec.handler(**clean_args)
        except Exception as exc:  # tool faults must not crash the loop
            logger.warning("Tool %s failed: %s", name, exc)
            raw = {
                "result": "error",
                "error_type": "ToolError",
                "message": f"Tool '{name}' execution failed: {exc}",
            }
        duration_ms = int((time.monotonic() - started) * 1000)
        raw.setdefault("_meta", {})["duration_ms"] = duration_ms
        self._cache[key] = raw
        return raw
