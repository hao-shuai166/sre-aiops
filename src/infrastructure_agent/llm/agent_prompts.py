"""Prompts for the P1 ReAct investigation loop.

The agent node asks the LLM one question per turn: "which tool next, or
conclude?" — the answer is a strict JSON object validated by the workflow.
When the step budget is exhausted, a forced-final prompt asks for a
best-effort conclusion based on the evidence already collected.
"""

import json

from infrastructure_agent.domain.models import Evidence
from infrastructure_agent.tools.registry import ToolSpec

AGENT_SYSTEM_PROMPT = """\
You are an expert Kubernetes SRE conducting a live incident investigation.
You autonomously decide, step by step, which tool to call next — or when the
evidence is sufficient to conclude with a final diagnosis.

Investigation guidelines:
1. Investigate like a real SRE: check Pod status first, then Events (Kubernetes
   hides the real fault type in event messages: FailedScheduling, ImagePullBackOff,
   OOMKilled, FailedMount...), then logs/metrics depending on the failure mode.
2. Base everything STRICTLY on the tool results shown below — never fabricate facts.
3. Never repeat a tool call with identical arguments — those results are already
   in the history. Pick a different tool or conclude.
4. Typical investigations need 2-4 tool calls. Conclude as soon as the evidence
   is sufficient; do not over-investigate.
5. The user's description may be imprecise (wrong pod name, wrong status word) —
   verify with tools before trusting it.

Output MUST be a single JSON object, in one of two shapes:

To call the next tool:
{
  "next": "tool",
  "thought": "one-line reason why this tool is needed now",
  "tool": "<exact tool name from the list>",
  "args": {"<parameter>": "<value>"}
}

To conclude the investigation:
{
  "next": "answer",
  "thought": "one-line summary of what the evidence shows",
  "problem": "问题定性（中文，如：Pod OOMKilled）",
  "root_cause": "根因分析结论（中文，引用证据 ID，如 ev002）",
  "suggestion": "可操作的修复建议（中文，分号分隔多条）",
  "confidence": 0.85
}
"""

# Content budget per evidence item / tool result, to keep the prompt small.
_EVIDENCE_CHARS = 900
_RESULT_CHARS = 400


def _format_tools(tools: list[ToolSpec]) -> str:
    lines = []
    for t in tools:
        schema = json.dumps(t.parameters_schema, ensure_ascii=False)
        lines.append(f"- {t.name}: {t.description}")
        lines.append(f"  args schema: {schema}")
    return "\n".join(lines)


def _format_evidence(evidence_list: list[Evidence]) -> str:
    if not evidence_list:
        return "（暂无）"
    lines = []
    for ev in evidence_list:
        content_str = json.dumps(ev.content, ensure_ascii=False, default=str)
        if len(content_str) > _EVIDENCE_CHARS:
            content_str = content_str[:_EVIDENCE_CHARS] + "...(truncated)"
        lines.append(f"- [{ev.id}] {ev.type} ({ev.resource.namespace}/{ev.resource.pod}): {content_str}")
    return "\n".join(lines)


def _format_history(tool_results: list[dict]) -> str:
    if not tool_results:
        return "（暂无）"
    lines = []
    for r in tool_results:
        args_str = json.dumps(r.get("args", {}), ensure_ascii=False)
        lines.append(f"- 第{r.get('step', '?')}步 {r.get('tool', '?')}{args_str} → {r.get('summary', '')}")
    return "\n".join(lines)


def _context_block(
    user_input: str,
    candidate_pod: str,
    candidate_ns: str,
    tools: list[ToolSpec],
    evidence_list: list[Evidence],
    tool_results: list[dict],
    step: int,
    max_steps: int,
) -> str:
    """Shared context injected into both decision and forced-final prompts."""
    return f"""\
## 用户问题
{user_input}

## 候选目标（规则预解析，可能有误，请用工具验证）
Pod: {candidate_pod}   Namespace: {candidate_ns}

## 可用工具
{_format_tools(tools)}

## 工具调用历史
{_format_history(tool_results)}

## 已收集证据
{_format_evidence(evidence_list)}

## 当前进度
第 {step}/{max_steps} 步
"""


def build_agent_decision_prompt(
    user_input: str,
    candidate_pod: str,
    candidate_ns: str,
    tools: list[ToolSpec],
    evidence_list: list[Evidence],
    tool_results: list[dict],
    step: int,
    max_steps: int,
) -> str:
    """Build the per-turn decision prompt for the ReAct loop."""
    return f"""{_context_block(user_input, candidate_pod, candidate_ns, tools, evidence_list, tool_results, step, max_steps)}

## 任务
决定下一步：调用一个工具（next="tool"）或给出最终诊断（next="answer"）。
只输出一个 JSON 对象，不要输出其他文字。"""


def build_forced_final_prompt(
    user_input: str,
    candidate_pod: str,
    candidate_ns: str,
    tools: list[ToolSpec],
    evidence_list: list[Evidence],
    tool_results: list[dict],
    step: int,
    max_steps: int,
) -> str:
    """Build the forced-final prompt used when the step budget is exhausted."""
    return f"""{_context_block(user_input, candidate_pod, candidate_ns, tools, evidence_list, tool_results, step, max_steps)}

## 任务
调查步数已达上限，不能再调用任何工具。请基于已收集的证据给出最佳判断：
next 必须为 "answer"。如果证据不足以确定根因，如实说明，降低 confidence，
并在 suggestion 中列出还需要哪些证据。只输出一个 JSON 对象，不要输出其他文字。"""
