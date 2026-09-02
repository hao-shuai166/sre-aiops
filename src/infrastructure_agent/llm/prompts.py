"""Prompt templates for LLM-based RCA (Root Cause Analysis).

All prompts are designed for OpenAI-compatible chat completion API.
The system prompt sets the SRE expert persona; user prompts inject
collected Evidence and Reasoning steps as analysis context.
"""

from infrastructure_agent.domain.models import Evidence, ReasoningStep


RCA_SYSTEM_PROMPT = """\
You are an expert SRE (Site Reliability Engineer) specializing in Kubernetes troubleshooting.
Your task is to analyze diagnostic evidence collected from a Kubernetes cluster and produce
a precise Root Cause Analysis (RCA) report.

Guidelines:
1. Base your analysis STRICTLY on the provided evidence — do NOT fabricate or assume facts
   that are not present in the evidence.
2. Cross-reference multiple evidence types (PodStatus, KubernetesEvent, ContainerLog, Metric)
   when available — do not rely on a single data point.
3. If evidence is contradictory, note the contradiction and assign lower confidence.
4. If evidence is insufficient for a definitive conclusion, state what additional evidence
   would be needed (e.g., "需要应用日志确认具体错误堆栈").
5. Suggestions must be actionable and specific — avoid vague advice like "检查配置".
   Instead, say "检查 ConfigMap 'app-config' 中 DATABASE_URL 的值是否正确".
6. Confidence scores:
   - 0.90-0.95: Multiple evidence types consistently point to the same root cause
   - 0.75-0.89: Strong indicators from 1-2 evidence types
   - 0.50-0.74: Partial evidence, further investigation recommended
   - 0.30-0.49: Weak signals only

Output format (MUST be valid JSON):
{
  "problem": "简明的问题描述",
  "root_cause": "根因分析结论，包含关键证据引用",
  "suggestion": "可操作的修复建议，分条列出",
  "confidence": 0.85
}
"""


def _format_evidence(evidence_list: list[Evidence]) -> str:
    """Format a list of Evidence objects into a readable text block."""
    if not evidence_list:
        return "（无证据）"

    lines: list[str] = []
    for ev in evidence_list:
        lines.append(f"### {ev.id} | Type: {ev.type} | Source: {ev.source.system}/{ev.source.api}")
        lines.append(f"Resource: {ev.resource.namespace}/{ev.resource.pod}"
                      + (f"/{ev.resource.container}" if ev.resource.container else ""))
        lines.append(f"Confidence: {ev.confidence}")
        lines.append("Content:")
        for key, value in ev.content.items():
            if isinstance(value, list):
                lines.append(f"  {key}:")
                for item in value[:20]:  # cap at 20 items to avoid token blow-up
                    lines.append(f"    - {item}")
            else:
                lines.append(f"  {key}: {value}")
        lines.append("")
    return "\n".join(lines)


def _format_reasoning(steps: list[ReasoningStep]) -> str:
    """Format reasoning steps into a readable text block."""
    if not steps:
        return "（无推理步骤）"

    lines: list[str] = []
    for s in steps:
        lines.append(f"Step {s.step}:")
        lines.append(f"  Observation: {s.observation}")
        lines.append(f"  Conclusion: {s.conclusion}")
    return "\n".join(lines)


def build_rca_prompt(
    evidence_list: list[Evidence],
    reasoning_steps: list[ReasoningStep],
    user_input: str,
) -> str:
    """Build the user prompt for RCA analysis by injecting evidence and reasoning context.

    Args:
        evidence_list: All collected Evidence objects from AgentState.
        reasoning_steps: The reasoning chain built during the diagnosis workflow.
        user_input: The user's original problem description.

    Returns:
        A formatted user prompt string ready for the LLM chat completion API.
    """
    evidence_text = _format_evidence(evidence_list)
    reasoning_text = _format_reasoning(reasoning_steps)

    return f"""\
## User's Problem Description
{user_input}

## Collected Evidence
{evidence_text}

## Reasoning Steps Taken
{reasoning_text}

## Task
Based on the evidence and reasoning above, produce a Root Cause Analysis report.
Output MUST be a single JSON object with exactly these fields:
- "problem": A concise summary of the problem (Chinese, 1-2 sentences)
- "root_cause": The root cause analysis conclusion, referencing specific evidence IDs (Chinese)
- "suggestion": Actionable fix suggestions, as a single Chinese string with numbered items separated by semicolons
- "confidence": A float between 0.0 and 1.0

Output ONLY the JSON object, no other text."""
