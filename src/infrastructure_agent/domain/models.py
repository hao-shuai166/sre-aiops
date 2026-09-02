"""Infrastructure Agent Domain Models.

All core data structures defined per agent-state-design.md and tool-design.md.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EvidenceSource(BaseModel):
    """Data source identifier — two-level: system + specific API."""

    system: str = Field(description="Infrastructure system: kubernetes/prometheus/loki/apm")
    api: str = Field(description="Specific API endpoint: events/logs/metrics/traces")


class EvidenceResource(BaseModel):
    """The Kubernetes resource this evidence is about."""

    namespace: str = Field(description="Kubernetes namespace")
    pod: str = Field(description="Pod name")
    container: Optional[str] = Field(default=None, description="Container name, if applicable")


class Evidence(BaseModel):
    """7-dimension structured evidence per state-design.md §8.

    This is the ONLY format that enters Agent State. Every MCP Tool's raw response
    must pass through EvidenceBuilder (Tool Layer) before being stored.
    """

    id: str = Field(description="Unique evidence identifier, e.g. ev001")
    type: str = Field(
        description="Evidence type: PodStatus / Event / Log / Metric / Alert / Trace"
    )
    source: EvidenceSource = Field(description="Where the data came from")
    timestamp: datetime = Field(description="When the evidence was collected")
    resource: EvidenceResource = Field(description="Target Kubernetes resource")
    content: dict[str, Any] = Field(description="Actual data payload")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of this evidence (0.0-1.0)",
    )


class ReasoningStep(BaseModel):
    """A single step in the agent's reasoning chain, per state-design.md §11."""

    step: int = Field(description="Step number in the reasoning sequence")
    observation: str = Field(description="What the agent observed at this step")
    conclusion: str = Field(description="The agent's conclusion from this observation")


class ReasoningControl(BaseModel):
    """Loop termination controls, per state-design.md §13.

    The Reasoning Loop stops when ANY of the three conditions is met:
    - iteration >= max_iteration (hard limit)
    - confidence >= confidence_threshold (quality threshold)
    - need_more_evidence == False (evidence sufficiency)
    """

    iteration: int = Field(default=0, description="Current iteration count")
    max_iteration: int = Field(default=5, description="Maximum allowed iterations (hard limit)")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Current diagnosis confidence"
    )
    need_more_evidence: bool = Field(
        default=True, description="Whether more evidence is needed to reach a conclusion"
    )

    @property
    def should_stop(self) -> bool:
        """Check if the reasoning loop should terminate."""
        if self.iteration >= self.max_iteration:
            return True
        if self.confidence >= 0.85:  # confidence_threshold
            return True
        if not self.need_more_evidence:
            return True
        return False


class RequestContext(BaseModel):
    """Original user request, per state-design.md §5."""

    user_input: str = Field(default="", description="The user's original question / problem description")
    user: str = Field(default="anonymous", description="User identifier")
    timestamp: datetime = Field(default_factory=datetime.now, description="Request timestamp")


class IntentState(BaseModel):
    """Agent's understanding of the user's problem, per state-design.md §6."""

    domain: Optional[str] = Field(
        default=None, description="Problem domain: kubernetes / monitoring / apm"
    )
    problem_type: Optional[str] = Field(
        default=None, description="Problem type: pod_failure / slow_performance / config_error"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Intent confidence")


class ExecutionState(BaseModel):
    """Current workflow execution status, per state-design.md §7."""

    current_workflow: Optional[str] = Field(
        default=None, description="Currently running workflow name"
    )
    current_step: Optional[str] = Field(default=None, description="Current step name")
    status: str = Field(
        default="pending", description="Execution status: pending/running/completed/error"
    )
    iteration: int = Field(default=0, description="Current iteration within the workflow")


class Diagnosis(BaseModel):
    """Final diagnosis output, per state-design.md §12.

    References evidence by ID only — full evidence is in AgentState.evidence.
    """

    problem: str = Field(description="Problem summary, e.g. 'Pod CrashLoopBackOff'")
    root_cause: str = Field(description="Root cause analysis result")
    evidence: list[str] = Field(
        default_factory=list, description="Evidence IDs that support this diagnosis"
    )
    suggestion: str = Field(description="Actionable suggestion to fix the problem")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall diagnosis confidence"
    )


class AgentState(BaseModel):
    """Unified Agent State — shared context across all LangGraph nodes.

    Per agent-state-design.md §4, this is NOT a sequential step in the pipeline.
    It is a side-car shared context that all nodes (Intent / Workflow / Tool) read from and write to.
    """

    request: RequestContext = Field(default_factory=RequestContext)
    intent: IntentState = Field(default_factory=IntentState)
    execution: ExecutionState = Field(default_factory=ExecutionState)
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning: list[ReasoningStep] = Field(default_factory=list)
    reasoning_control: ReasoningControl = Field(default_factory=ReasoningControl)
    diagnosis: Optional[Diagnosis] = Field(default=None)
