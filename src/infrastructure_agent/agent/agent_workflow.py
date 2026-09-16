"""ReAct Investigation Workflow — LLM autonomously selects tools.

P1 replacement for the fixed pod_crash_workflow graph:

    init ─▶ agent ──(next=tool)──▶ execute_tool ─▶ agent ...
              │
              └─(next=answer / error)──▶ END

- ``agent`` node: injects user question + tool schemas + evidence + call
  history into the prompt; the LLM replies with either a tool call or a
  final answer (strict JSON).
- ``execute_tool`` node: executes the tool through the ToolRegistry
  (validated + memoized), converts the result to 7-dim Evidence and appends
  a reasoning step.
- Step budget: MAX_STEPS tool calls. When exhausted, the agent node switches
  to a forced-final prompt (best-effort answer). If the LLM fails entirely,
  a structured error is returned — there is no rule-based fallback by design.

The fixed workflow stays available on this branch for A/B comparison via
AGENT_WORKFLOW=fixed.
"""

import contextvars
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph

from infrastructure_agent.domain.models import (
    AgentState,
    Diagnosis,
    Evidence,
    ExecutionState,
    IntentState,
    ReasoningControl,
    ReasoningStep,
    RequestContext,
)
from infrastructure_agent.llm import get_llm_client
from infrastructure_agent.llm.agent_prompts import (
    AGENT_SYSTEM_PROMPT,
    build_agent_decision_prompt,
    build_forced_final_prompt,
)
from infrastructure_agent.tools.evidence_builder import EvidenceBuilder
from infrastructure_agent.tools.k8s_tools import tool_registry
from infrastructure_agent.tools.registry import ToolCache
from infrastructure_agent.workflow.pod_crash_workflow import (
    _parse_namespace,
    _parse_pod_name,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 8


# ---- Request-scoped runtime ------------------------------------------------
#
# The compiled LangGraph graph is a process-level singleton, but mutable
# per-investigation state (memoization cache + evidence counter) must NEVER
# be. Each investigation creates one InvestigationRuntime, propagated to the
# graph nodes via ContextVar — safe under concurrent asyncio requests because
# every task tree copies its own context.


@dataclass
class InvestigationRuntime:
    """Mutable state owned by a single investigation."""

    cache: ToolCache = field(default_factory=ToolCache)
    builder: EvidenceBuilder = field(default_factory=EvidenceBuilder)


_runtime_var: contextvars.ContextVar[InvestigationRuntime] = contextvars.ContextVar(
    "investigation_runtime"
)


def start_investigation_runtime() -> InvestigationRuntime:
    """Create and install a fresh runtime for the current investigation.

    Must be called ONCE per diagnosis, BEFORE graph.ainvoke() — setting the
    ContextVar in the caller's task guarantees visibility in every node the
    graph spawns (child tasks copy the parent context).
    """
    runtime = InvestigationRuntime()
    _runtime_var.set(runtime)
    return runtime


def _current_runtime() -> InvestigationRuntime:
    """Runtime for the running investigation; creates one as a safety net
    when the graph is invoked directly (e.g. tests) without setup."""
    try:
        return _runtime_var.get()
    except LookupError:
        runtime = InvestigationRuntime()
        _runtime_var.set(runtime)
        return runtime


class InvestigationState(AgentState):
    """State for the ReAct loop. Field names MUST NOT start with underscore
    (Pydantic v2 drops private fields during LangGraph serialization)."""

    wf_pod: str = "nginx"
    wf_namespace: str = "default"
    wf_cluster: str = "prod"
    rca_mode: str = "unknown"
    step: int = 0
    decision: dict = {}
    tool_results: list[dict] = []
    error: str = ""


# ---- Node: Initialize (rule-based candidate parsing — a hint, not truth) ----

def init_node(state: InvestigationState) -> dict:
    user_input = state.request.user_input or ""
    return {
        "request": RequestContext(
            user_input=user_input,
            user=state.request.user,
            timestamp=datetime.now(timezone.utc),
        ),
        "intent": IntentState(
            domain="kubernetes",
            problem_type="pod_failure",
            confidence=0.90,
        ),
        "execution": ExecutionState(
            current_workflow="agent_investigation",
            current_step="agent",
            status="running",
            iteration=0,
        ),
        "reasoning_control": ReasoningControl(
            iteration=0,
            max_iteration=MAX_STEPS,
            confidence=0.0,
            need_more_evidence=True,
        ),
        "wf_pod": _parse_pod_name(user_input),
        "wf_namespace": _parse_namespace(user_input),
        "step": 0,
        "decision": {},
        "tool_results": [],
        "error": "",
        "rca_mode": "unknown",
    }


# ---- Decision validation ----

def _valid_tool_decision(d: object) -> bool:
    return (
        isinstance(d, dict)
        and d.get("next") == "tool"
        and isinstance(d.get("tool"), str)
        and tool_registry.get(d["tool"]) is not None
        and isinstance(d.get("args") or {}, dict)
    )


def _valid_answer_decision(d: object) -> bool:
    if not isinstance(d, dict) or d.get("next") != "answer":
        return False
    if not all(
        isinstance(d.get(k), str) and d[k].strip() for k in ("problem", "root_cause", "suggestion")
    ):
        return False
    refs = d.get("evidence")
    return isinstance(refs, list) and len(refs) > 0 and all(
        isinstance(r, str) and r.strip() for r in refs
    )


def _evidence_ref_error(decision: dict, evidence_list: list[Evidence]) -> str | None:
    """Cross-check the answer's evidence references against collected evidence.

    Rules (evidence-driven trust):
    - every referenced ID must exist in this investigation;
    - at least one referenced evidence must be non-error (confidence > 0).
      Error evidence (e.g. NotAvailable) is legitimate exclusionary support,
      but it cannot be the ONLY support for a conclusion.

    Returns None when acceptable, else a reason for the corrective retry.
    """
    refs = decision.get("evidence") or []
    known = {ev.id for ev in evidence_list}
    unknown = [r for r in refs if r not in known]
    if unknown:
        valid = ", ".join(sorted(known)) or "（本次调查没有任何证据）"
        return f"引用了本次调查中不存在的证据 ID：{', '.join(unknown)}；有效 ID：{valid}"
    if not any(ev.confidence > 0.0 for ev in evidence_list if ev.id in refs):
        return (
            "结论的 evidence 至少要包含一条有效证据（confidence > 0）。"
            "错误类证据只能作为排除性依据，不能单独支持结论"
        )
    return None


def _decision_error(
    decision: object, forced: bool, evidence_list: list[Evidence]
) -> str | None:
    """Full validation of an LLM decision; None means acceptable."""
    if isinstance(decision, dict) and decision.get("next") == "tool":
        if forced:
            return '步数已耗尽，禁止再调用工具；next 必须为 "answer"'
        if tool_registry.get(decision.get("tool", "")) is None:
            known = ", ".join(tool_registry.tool_names())
            return f"工具名 {decision.get('tool')!r} 不存在；可用工具：{known}"
        if not isinstance(decision.get("args") or {}, dict):
            return "tool 决策的 args 必须是对象"
        return None
    if _valid_answer_decision(decision):
        assert isinstance(decision, dict)
        return _evidence_ref_error(decision, evidence_list)
    return (
        "输出必须是 next=\"tool\"（含合法 tool/args）或 next=\"answer\""
        "（含 problem/root_cause/suggestion/evidence/confidence，其中 evidence "
        "为非空字符串数组）之一的 JSON 对象"
    )


def _clamp_confidence(value: object) -> float:
    try:
        c = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        c = 0.5
    return max(0.0, min(1.0, c))


def _retry_note(error: str) -> str:
    """Correction hint appended when the LLM produced an invalid decision."""
    return f"\n\n## 上一次输出无效\n{error}。请重新输出一个合法 JSON 对象。"


# ---- Node: Agent (LLM decision) ----

async def agent_node(state: InvestigationState) -> dict:
    llm = get_llm_client()
    if not llm.available:
        return {
            "rca_mode": "error",
            "error": "LLM 不可用（未配置 OPENAI_API_KEY 或初始化失败），自主调查无法运行",
            "execution": ExecutionState(
                current_workflow="agent_investigation",
                current_step="agent",
                status="error",
                iteration=state.step,
            ),
        }

    tools = tool_registry.list_tools()
    forced = state.step >= MAX_STEPS
    common_args = dict(
        user_input=state.request.user_input,
        candidate_pod=state.wf_pod,
        candidate_ns=state.wf_namespace,
        tools=tools,
        evidence_list=state.evidence,
        tool_results=state.tool_results,
        step=state.step,
        max_steps=MAX_STEPS,
    )
    if forced:
        prompt = build_forced_final_prompt(**common_args)
    else:
        prompt = build_agent_decision_prompt(**common_args)

    decision = await llm.generate_structured(
        system_prompt=AGENT_SYSTEM_PROMPT,
        user_prompt=prompt,
        temperature=0.2,
        max_tokens=1200,
    )
    error = _decision_error(decision, forced, state.evidence)

    if error:
        # One retry with a targeted correction hint.
        decision = await llm.generate_structured(
            system_prompt=AGENT_SYSTEM_PROMPT,
            user_prompt=prompt + _retry_note(error),
            temperature=0.2,
            max_tokens=1200,
        )
        error = _decision_error(decision, forced, state.evidence)

    if error:
        return {
            "rca_mode": "error",
            "error": f"LLM 决策无效（重试后仍不通过）：{error}。调查中断",
            "execution": ExecutionState(
                current_workflow="agent_investigation",
                current_step="agent",
                status="error",
                iteration=state.step,
            ),
        }

    if decision["next"] == "answer":
        diagnosis = Diagnosis(
            problem=decision["problem"],
            root_cause=decision["root_cause"],
            evidence=decision["evidence"],
            suggestion=decision["suggestion"],
            confidence=_clamp_confidence(decision.get("confidence")),
        )
        return {
            "diagnosis": diagnosis,
            "rca_mode": "llm",
            "decision": {},
            "reasoning": state.reasoning + [
                ReasoningStep(
                    step=len(state.reasoning) + 1,
                    observation=(
                        f"LLM concluded after {state.step} tool calls: "
                        f"{decision['problem']}"
                    ),
                    conclusion=decision["root_cause"],
                )
            ],
            "reasoning_control": ReasoningControl(
                iteration=state.step,
                max_iteration=MAX_STEPS,
                confidence=diagnosis.confidence,
                need_more_evidence=False,
            ),
            "execution": ExecutionState(
                current_workflow="agent_investigation",
                current_step="done",
                status="completed",
                iteration=state.step,
            ),
        }

    # next == "tool"
    thought = str(decision.get("thought", "")).strip()
    return {
        "decision": decision,
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Agent decision: call {decision['tool']}({json.dumps(decision.get('args') or {}, ensure_ascii=False)})",
                conclusion=thought or "继续调查",
            )
        ],
        "execution": ExecutionState(
            current_workflow="agent_investigation",
            current_step="execute_tool",
            status="running",
            iteration=state.step,
        ),
    }


# ---- Node: Execute tool ----

def _summarize_raw(raw: dict) -> str:
    text = json.dumps(raw, ensure_ascii=False, default=str)
    return text[:400] + ("...(truncated)" if len(text) > 400 else "")


async def execute_tool_node(state: InvestigationState) -> dict:
    decision = state.decision or {}
    name = decision.get("tool", "")
    args = decision.get("args") or {}
    # Merge defaults from the parsed candidates so the LLM can omit them.
    if "pod" not in args:
        args["pod"] = state.wf_pod
    if "namespace" not in args:
        args["namespace"] = state.wf_namespace

    spec = tool_registry.get(name)
    runtime = _current_runtime()
    raw = await tool_registry.call(name, args, cache=runtime.cache)

    step = state.step + 1
    updates: dict = {
        "step": step,
        "decision": {},
        "tool_results": state.tool_results + [
            {
                "step": step,
                "tool": name,
                "args": args,
                "summary": _summarize_raw(raw),
            }
        ],
        "execution": ExecutionState(
            current_workflow="agent_investigation",
            current_step="agent",
            status="running",
            iteration=step,
        ),
    }

    ns = str(args.get("namespace", state.wf_namespace))
    pod = str(args.get("pod", state.wf_pod))

    if spec is not None and spec.produces_evidence:
        evidence: Evidence = runtime.builder.build_from_tool_result(
            tool_name=name,
            raw=raw,
            namespace=ns,
            pod=pod,
            container=args.get("container"),
        )
        updates["evidence"] = state.evidence + [evidence]

    updates["reasoning"] = state.reasoning + [
        ReasoningStep(
            step=len(state.reasoning) + 1,
            observation=f"Tool {name} → {_summarize_raw(raw)}",
            # Deliberately NOT the decision's thought (already shown on the
            # previous step) — this step only records that the tool ran.
            conclusion=f"工具 {name} 执行完毕，结果已返回，等待 Agent 下一步决策",
        )
    ]
    return updates


# ---- Routing ----

def route_after_agent(state: InvestigationState) -> str:
    if state.error or state.diagnosis is not None:
        return "end"
    return "execute_tool"


# ---- Graph construction ----

def build_investigation_graph():
    """Compile the ReAct investigation graph."""
    graph = StateGraph(InvestigationState)
    graph.add_node("init", init_node)
    graph.add_node("agent", agent_node)
    graph.add_node("execute_tool", execute_tool_node)
    graph.set_entry_point("init")
    graph.add_edge("init", "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"execute_tool": "execute_tool", "end": END},
    )
    graph.add_edge("execute_tool", "agent")
    return graph.compile()
