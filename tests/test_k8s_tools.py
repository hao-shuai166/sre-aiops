"""Unit tests for tool-layer ④ — tail integer schema + internal-query audit.

Covers:
- tail coerced/clamped to [1, 200] incl. string input from the LLM;
- the registry schema declares tail as integer with minimum/maximum;
- get_container_logs records ``_meta.container_source`` (explicit vs
  auto_first_container) and the internal get_pod query it performed;
- the audit metadata surfaces in the ContainerLog Evidence content.
"""

import pytest

from infrastructure_agent.tools.evidence_builder import EvidenceBuilder
from infrastructure_agent.tools.k8s_tools import (
    DEFAULT_TAIL,
    MAX_TAIL,
    _coerce_tail,
    get_container_logs,
    tool_registry,
)


# ---- _coerce_tail ----

def test_coerce_tail_int_passthrough():
    assert _coerce_tail(50) == 50


def test_coerce_tail_numeric_string():
    # LLM occasionally emits strings — coerce, don't crash
    assert _coerce_tail("50") == 50


def test_coerce_tail_clamps_low():
    assert _coerce_tail(0) == 1
    assert _coerce_tail(-5) == 1


def test_coerce_tail_clamps_high():
    assert _coerce_tail(9999) == MAX_TAIL


def test_coerce_tail_garbage_falls_back_to_default():
    assert _coerce_tail("abc") == DEFAULT_TAIL
    assert _coerce_tail(None) == DEFAULT_TAIL


# ---- schema declaration ----

def test_tail_schema_is_integer_with_bounds():
    spec = tool_registry.get("get_container_logs")
    assert spec is not None
    tail = spec.parameters_schema["properties"]["tail"]
    assert tail["type"] == "integer"
    assert tail["minimum"] == 1
    assert tail["maximum"] == MAX_TAIL
    assert tail["default"] == DEFAULT_TAIL


# ---- get_container_logs audit metadata (mock mode) ----

@pytest.mark.asyncio
async def test_auto_container_records_internal_query():
    raw = await get_container_logs(namespace="default", pod="nginx-oom")
    meta = raw["_meta"]
    assert meta["container"] == "app"
    assert meta["container_source"] == "auto_first_container"
    assert meta["internal_queries"] == [
        {"tool": "get_pod_status", "purpose": "resolve first container"}
    ]
    assert meta["tail"] == DEFAULT_TAIL


@pytest.mark.asyncio
async def test_explicit_container_has_no_internal_query():
    raw = await get_container_logs(
        namespace="default", pod="nginx-oom", container="app", tail=2
    )
    meta = raw["_meta"]
    assert meta["container_source"] == "explicit"
    assert "internal_queries" not in meta
    assert meta["tail"] == 2


@pytest.mark.asyncio
async def test_tail_clamped_in_meta():
    raw = await get_container_logs(
        namespace="default", pod="nginx-oom", container="app", tail="500"
    )
    assert raw["_meta"]["tail"] == MAX_TAIL


# ---- metadata surfaces in Evidence ----

@pytest.mark.asyncio
async def test_container_log_evidence_carries_audit_metadata():
    raw = await get_container_logs(namespace="default", pod="nginx-oom")
    builder = EvidenceBuilder()
    ev = builder.build_from_tool_result(
        "get_container_logs", raw, namespace="default", pod="nginx-oom"
    )
    assert ev.type == "ContainerLog"
    assert ev.content["container_source"] == "auto_first_container"
    assert ev.content["tail"] == DEFAULT_TAIL
