"""Evidence Builder — Tool Layer component that converts MCP raw responses into 7-dim Evidence.

Per tool-design.md §3 and §14, this is a shared Tool Layer component.
All MCP Servers return raw responses; Evidence Builder standardizes them into the
Evidence format defined in state-design.md §8 before writing into AgentState.
"""

from datetime import datetime, timezone

from infrastructure_agent.domain.models import Evidence, EvidenceResource, EvidenceSource


class EvidenceBuilder:
    """Converts raw MCP Tool responses into structured Evidence objects.

    Each Tool type has a dedicated builder method that maps its raw response fields
    to the 7-dimension Evidence structure (id / type / source / timestamp / resource / content / confidence).
    """

    def __init__(self):
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"ev{self._counter:03d}"

    def _make_source(self, system: str, api: str) -> EvidenceSource:
        return EvidenceSource(system=system, api=api)

    def _make_resource(
        self, namespace: str, pod: str, container: str | None = None
    ) -> EvidenceResource:
        return EvidenceResource(namespace=namespace, pod=pod, container=container)

    # ---- Per-tool converters ----

    def _is_error(self, raw: dict) -> bool:
        """Check if raw response is an error (no data to build evidence from)."""
        return raw.get("result") == "error"

    def _error_evidence(
        self, type_name: str, namespace: str, pod: str, container: str | None, raw: dict
    ) -> Evidence:
        """Build a low-confidence evidence object from an error response."""
        return Evidence(
            id=self._next_id(),
            type=type_name,
            source=self._make_source("kubernetes", type_name.lower()),
            timestamp=datetime.now(timezone.utc),
            resource=self._make_resource(namespace, pod, container),
            content={
                "error": raw.get("error_type", "Unknown"),
                "message": raw.get("message", "Unknown error"),
                "raw": raw,
            },
            confidence=0.0,
        )

    def build_from_get_pod(
        self,
        raw: dict,
        namespace: str,
        pod: str,
    ) -> Evidence:
        """Convert get_pod() response → Evidence. Per tool-design.md §7.1."""
        if self._is_error(raw):
            return self._error_evidence("PodStatus", namespace, pod, None, raw)
        data = raw["data"]
        containers = data["containers"]

        # Extract lastState info from container statuses for CrashLoopBackOff diagnosis
        last_exit_code = data.get("exit_code")
        last_reason = None
        last_message = None
        for c in containers:
            if c.get("last_exit_code") is not None and last_exit_code is None:
                last_exit_code = c["last_exit_code"]
            if c.get("last_reason") and last_reason is None:
                last_reason = c["last_reason"]
            if c.get("last_message") and last_message is None:
                last_message = c["last_message"]

        content = {
            "status": data["status"],
            "restart_count": data["restart_count"],
            "containers": containers,
            "exit_code": last_exit_code,
            "reason": data.get("reason"),
        }
        if last_reason:
            content["last_reason"] = last_reason
        if last_message:
            content["last_message"] = last_message

        return Evidence(
            id=self._next_id(),
            type="PodStatus",
            source=self._make_source("kubernetes", "pods"),
            timestamp=datetime.now(timezone.utc),
            resource=self._make_resource(namespace, pod),
            content=content,
            confidence=0.95,
        )

    def build_from_get_events(
        self,
        raw: dict,
        namespace: str,
        pod: str,
    ) -> Evidence:
        """Convert get_events() response → Evidence. Per tool-design.md §7.2."""
        if self._is_error(raw):
            return self._error_evidence("KubernetesEvent", namespace, pod, None, raw)
        data = raw["data"]
        return Evidence(
            id=self._next_id(),
            type="KubernetesEvent",
            source=self._make_source("kubernetes", "events"),
            timestamp=datetime.now(timezone.utc),
            resource=self._make_resource(namespace, pod),
            content={"events": data["events"]},
            confidence=0.95,
        )

    def build_from_get_logs(
        self,
        raw: dict,
        namespace: str,
        pod: str,
        container: str,
    ) -> Evidence:
        """Convert get_logs() response → Evidence. Per tool-design.md §7.3."""
        if self._is_error(raw):
            return self._error_evidence("ContainerLog", namespace, pod, container, raw)
        data = raw["data"]
        return Evidence(
            id=self._next_id(),
            type="ContainerLog",
            source=self._make_source("kubernetes", "logs"),
            timestamp=datetime.now(timezone.utc),
            resource=self._make_resource(namespace, pod, container),
            content={
                "container": data["container"],
                "logs": data["logs"],
            },
            confidence=0.90,
        )

    def reset_counter(self) -> None:
        """Reset evidence ID counter. Useful for testing."""
        self._counter = 0
