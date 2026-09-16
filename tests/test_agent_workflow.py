"""Unit tests for the ReAct decision-validation layer (no LLM involved).

Covers the evidence-reference contract introduced with the
"evidence-driven trust" change:

- answer decisions MUST carry a non-empty ``evidence`` string array;
- every referenced ID must exist in the current investigation;
- at least one referenced evidence must be non-error (confidence > 0);
- tool decisions keep their existing validation semantics.
"""

from datetime import datetime, timezone

from infrastructure_agent.agent.agent_workflow import (
    _clamp_confidence,
    _decision_error,
    _evidence_ref_error,
    _valid_answer_decision,
    _valid_tool_decision,
)
from infrastructure_agent.domain.models import (
    Evidence,
    EvidenceResource,
    EvidenceSource,
)


def _evidence(ev_id: str, confidence: float = 0.95) -> Evidence:
    return Evidence(
        id=ev_id,
        type="PodStatus",
        source=EvidenceSource(system="kubernetes", api="pods"),
        timestamp=datetime.now(timezone.utc),
        resource=EvidenceResource(namespace="default", pod="nginx-oom"),
        content={"status": "CrashLoopBackOff"},
        confidence=confidence,
    )


_VALID_ANSWER = {
    "next": "answer",
    "problem": "Pod OOMKilled",
    "root_cause": "内存达到 Limit 512Mi（ev001）",
    "evidence": ["ev001"],
    "suggestion": "调高内存 Limit",
    "confidence": 0.9,
}


# ---- _valid_answer_decision ----

def test_answer_decision_requires_evidence_array():
    d = dict(_VALID_ANSWER, evidence=[])
    assert not _valid_answer_decision(d)
    d = dict(_VALID_ANSWER, evidence="ev001")
    assert not _valid_answer_decision(d)
    d = dict(_VALID_ANSWER, evidence=["ev001", ""])
    assert not _valid_answer_decision(d)


def test_answer_decision_accepts_valid_shape():
    assert _valid_answer_decision(_VALID_ANSWER)


def test_answer_decision_still_requires_text_fields():
    d = dict(_VALID_ANSWER, root_cause="  ")
    assert not _valid_answer_decision(d)


# ---- _evidence_ref_error ----

def test_ref_error_unknown_id():
    ev = [_evidence("ev001")]
    err = _evidence_ref_error(dict(_VALID_ANSWER, evidence=["ev999"]), ev)
    assert err is not None and "ev999" in err


def test_ref_error_only_error_evidence_rejected():
    # Two error evidences (confidence 0) — citing only them must fail.
    ev = [_evidence("ev001", confidence=0.0), _evidence("ev002", confidence=0.0)]
    err = _evidence_ref_error(dict(_VALID_ANSWER, evidence=["ev001", "ev002"]), ev)
    assert err is not None and "有效证据" in err


def test_ref_error_error_plus_valid_evidence_accepted():
    # NotAvailable evidence as exclusionary support + one valid evidence.
    ev = [_evidence("ev001", confidence=0.0), _evidence("ev002", confidence=0.95)]
    err = _evidence_ref_error(dict(_VALID_ANSWER, evidence=["ev001", "ev002"]), ev)
    assert err is None


def test_ref_error_empty_investigation():
    err = _evidence_ref_error(dict(_VALID_ANSWER, evidence=["ev001"]), [])
    assert err is not None


# ---- _decision_error ----

def test_decision_error_valid_tool():
    d = {"next": "tool", "tool": "get_pod_status", "args": {"pod": "nginx-oom"}}
    assert _decision_error(d, forced=False, evidence_list=[]) is None


def test_decision_error_unknown_tool():
    d = {"next": "tool", "tool": "delete_pod", "args": {"pod": "x"}}
    err = _decision_error(d, forced=False, evidence_list=[])
    assert err is not None and "delete_pod" in err


def test_decision_error_tool_forbidden_when_forced():
    d = {"next": "tool", "tool": "get_pod_status", "args": {}}
    err = _decision_error(d, forced=True, evidence_list=[])
    assert err is not None and "步数已耗尽" in err


def test_decision_error_answer_with_bad_refs():
    ev = [_evidence("ev001")]
    d = dict(_VALID_ANSWER, evidence=["ev404"])
    err = _decision_error(d, forced=False, evidence_list=ev)
    assert err is not None


def test_decision_error_answer_ok():
    ev = [_evidence("ev001")]
    assert _decision_error(_VALID_ANSWER, forced=False, evidence_list=ev) is None


# ---- _valid_tool_decision / _clamp_confidence ----

def test_valid_tool_decision():
    assert _valid_tool_decision({"next": "tool", "tool": "list_pods", "args": {"namespace": "default"}})
    assert not _valid_tool_decision({"next": "tool", "tool": "nope", "args": {}})
    # Missing args is allowed — execute_tool_node back-fills pod/namespace defaults.
    assert _valid_tool_decision({"next": "tool", "tool": "list_pods"})
    assert not _valid_tool_decision({"next": "tool", "tool": "list_pods", "args": "ns"})


def test_clamp_confidence():
    assert _clamp_confidence(1.5) == 1.0
    assert _clamp_confidence(-0.2) == 0.0
    assert _clamp_confidence("0.85") == 0.85
    assert _clamp_confidence(None) == 0.5
