""""Agent Layer — Intent classification and workflow routing.

Per agent-flow.md §4 and §5, the Agent Layer:
1. Receives user question
2. Classifies intent (keyword-based for V1, LLM-based for V2)
3. Routes to the appropriate LangGraph workflow
4. Returns the diagnosis result with full evidence chain
"""

import logging
import os
import traceback
from datetime import datetime, timezone

from infrastructure_agent.domain.models import (
    AgentState,
    RequestContext,
)
from infrastructure_agent.workflow.pod_crash_workflow import build_crashloop_graph

logger = logging.getLogger(__name__)

# Cached compiled graphs — built once per process
_react_graph = None
_crashloop_graph = None


def _get_react_graph():
    global _react_graph
    if _react_graph is None:
        from infrastructure_agent.agent.agent_workflow import build_investigation_graph
        _react_graph = build_investigation_graph()
    return _react_graph


def _get_crashloop_graph():
    global _crashloop_graph
    if _crashloop_graph is None:
        _crashloop_graph = build_crashloop_graph()
    return _crashloop_graph


def _get_workflow_graph():
    """Select the workflow implementation.

    AGENT_WORKFLOW=react (default) — P1 ReAct loop, LLM autonomously picks tools.
    AGENT_WORKFLOW=fixed         — legacy fixed graph, kept for A/B and rollback.
    """
    mode = os.getenv("AGENT_WORKFLOW", "react").lower()
    if mode == "fixed":
        return _get_crashloop_graph()
    return _get_react_graph()


# ---- Intent classification (keyword-based for V1) ----
POD_FAILURE_KEYWORDS = [
    "crashloopbackoff",
    "crash",
    "pod restart",
    "pod一直重启",
    "容器不断退出",
    "container restart",
    "back-off",
    "crash loop",
    "启动失败",
    "oom",
    "oomkilled",
    "imagepullbackoff",
]

SLOW_PERFORMANCE_KEYWORDS = [
    "速度慢",
    "响应慢",
    "延迟",
    "latency",
    "slow",
    "timeout",
    "超时",
    "访问慢",
    "调用慢",
]


def classify_intent(user_input: str) -> dict:
    """Classify user intent from natural language input.

    V1: simple keyword matching. V2: LLM-based classification.
    Returns intent dict compatible with IntentState.
    """
    lower = user_input.lower()

    # Check pod failure keywords
    for kw in POD_FAILURE_KEYWORDS:
        if kw in lower:
            return {
                "domain": "kubernetes",
                "problem_type": "pod_failure",
                "confidence": 0.90,
            }

    # Check slow performance keywords
    for kw in SLOW_PERFORMANCE_KEYWORDS:
        if kw in lower:
            return {
                "domain": "kubernetes",
                "problem_type": "slow_performance",
                "confidence": 0.80,
            }

    # Default: unknown, try pod failure workflow anyway
    return {
        "domain": "kubernetes",
        "problem_type": "pod_failure",
        "confidence": 0.50,
    }


async def diagnose(user_input: str, user: str = "anonymous") -> dict:
    """Run the full diagnosis pipeline for a user question.

    Args:
        user_input: The user's natural language question
        user: User identifier

    Returns:
        Dict with problem, root_cause, evidence, suggestion, confidence, reasoning_trace
    """
    intent = classify_intent(user_input)

    # Only pod_failure is implemented in V1
    if intent["problem_type"] == "pod_failure":
        return await _run_pod_diagnosis(user_input, user, intent)

    return {
        "problem": "unsupported",
        "root_cause": "",
        "evidence": [],
        "suggestion": f"暂不支持 {intent['problem_type']} 类问题，当前仅支持 Pod 故障诊断",
        "confidence": 0.0,
        "reasoning_trace": [],
    }


async def _run_pod_diagnosis(user_input: str, user: str, intent: dict) -> dict:
    """Run the investigation workflow and return structured results."""
    graph = _get_workflow_graph()

    initial_state = AgentState(
        request=RequestContext(
            user_input=user_input,
            user=user,
            timestamp=datetime.now(timezone.utc),
        ),
    )

    try:
        result = await graph.ainvoke(
            initial_state.model_dump(),
            config={"recursion_limit": 50},
        )
    except Exception as exc:
        logger.error(
            "Workflow execution failed for user_input=%r: %s\n%s",
            user_input, exc, traceback.format_exc(),
        )
        return {
            "problem": "diagnosis_error",
            "root_cause": f"诊断工作流执行异常: {exc}",
            "evidence": [],
            "suggestion": "请检查 Kubernetes 集群连通性、Pod 名称是否正确，以及 API Key 是否有效",
            "confidence": 0.0,
            "rca_mode": "error",
            "reasoning_trace": [{
                "step": 0,
                "observation": f"Exception: {exc}",
                "conclusion": "工作流中断，请查看服务端日志获取详细信息",
            }],
        }

    # rca_mode lives on the extended state, not AgentState — pull from raw result
    rca_mode = str(result.get("rca_mode", "unknown"))
    error_message = str(result.get("error", "") or "")

    final_state = AgentState(**result)

    diagnosis = final_state.diagnosis
    if diagnosis is None:
        if error_message or rca_mode == "error":
            # ReAct loop aborted (LLM unavailable / invalid decisions) —
            # by design there is no rule-based fallback (decision ①).
            return {
                "problem": "diagnosis_error",
                "root_cause": error_message or "LLM 调用失败，自主调查中断",
                "evidence": [ev.model_dump() for ev in final_state.evidence],
                "suggestion": "请确认 OPENAI_API_KEY 配置有效后重试；服务端日志中有详细失败原因",
                "confidence": 0.0,
                "rca_mode": "error",
                "reasoning_trace": [r.model_dump() for r in final_state.reasoning],
            }
        return {
            "problem": "incomplete",
            "root_cause": "诊断未完成",
            "evidence": [ev.model_dump() for ev in final_state.evidence],
            "suggestion": "请重试或提供更多信息",
            "confidence": 0.0,
            "rca_mode": rca_mode,
            "reasoning_trace": [r.model_dump() for r in final_state.reasoning],
        }

    evidence_details = []
    for ev_id in diagnosis.evidence:
        for ev in final_state.evidence:
            if ev.id == ev_id:
                evidence_details.append({
                    "id": ev.id,
                    "type": ev.type,
                    "source": f"{ev.source.system}/{ev.source.api}",
                    "content": ev.content,
                    "confidence": ev.confidence,
                })
                break

    return {
        "problem": diagnosis.problem,
        "root_cause": diagnosis.root_cause,
        "evidence": evidence_details,
        "suggestion": diagnosis.suggestion,
        "confidence": diagnosis.confidence,
        "rca_mode": rca_mode,
        "reasoning_trace": [r.model_dump() for r in final_state.reasoning],
    }
