"""Kubernetes client adapter — mock and real implementations.

Mock mode returns realistic predefined data for 5 diagnostic scenarios.
Real mode connects to a live cluster with 3-level config auto-detection:
  1. Explicit kubeconfig path or context
  2. In-cluster config (containerised deployment)
  3. Default kubeconfig (~/.kube/config or KUBECONFIG env var)

Switch via K8S_MODE env var or constructor parameter.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from kubernetes import client as k8s_client
from kubernetes import config

logger = logging.getLogger(__name__)


@dataclass
class PodStatus:
    status: str
    restart_count: int
    containers: list[dict[str, str]]
    exit_code: Optional[int] = None
    reason: Optional[str] = None


@dataclass
class Event:
    reason: str
    message: str
    type: str = "Warning"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LogEntry:
    lines: list[str]
    container: str


# ---- Mock data for development ----

MOCK_SCENARIOS = {
    "oom": {
        "pod": PodStatus(
            status="CrashLoopBackOff",
            restart_count=15,
            containers=[{"name": "app", "state": "Waiting", "reason": "CrashLoopBackOff"}],
            exit_code=137,
            reason="OOMKilled",
        ),
        "events": [
            Event(reason="OOMKilled", message="Container exceeded memory limit of 512Mi"),
            Event(reason="BackOff", message="Back-off restarting failed container"),
        ],
        "logs": LogEntry(
            container="app",
            lines=[
                "2026-07-24T09:00:01Z ERROR: failed to allocate memory",
                "2026-07-24T09:00:01Z FATAL: out of memory",
                "2026-07-24T09:00:02Z Killed by OOM killer",
            ],
        ),
    },
    "image_pull": {
        "pod": PodStatus(
            status="ImagePullBackOff",
            restart_count=5,
            containers=[{"name": "app", "state": "Waiting", "reason": "ImagePullBackOff"}],
        ),
        "events": [
            Event(
                reason="ErrImagePull",
                message='Failed to pull image "nginx:latest-v999": manifest unknown',
            ),
            Event(reason="ImagePullBackOff", message="Back-off pulling image"),
        ],
        "logs": LogEntry(container="app", lines=[]),
    },
    "config_error": {
        "pod": PodStatus(
            status="CrashLoopBackOff",
            restart_count=8,
            containers=[{"name": "app", "state": "Waiting", "reason": "CrashLoopBackOff"}],
            exit_code=1,
        ),
        "events": [
            Event(
                reason="FailedMount",
                message='MountVolume.SetUp failed for volume "config": secret "app-config" not found',
            ),
        ],
        "logs": LogEntry(
            container="app",
            lines=["2026-07-24T09:00:01Z ERROR: config file not found at /etc/app/config.yaml"],
        ),
    },
    "app_error": {
        "pod": PodStatus(
            status="CrashLoopBackOff",
            restart_count=12,
            containers=[{"name": "app", "state": "Waiting", "reason": "CrashLoopBackOff"}],
            exit_code=1,
        ),
        "events": [
            Event(reason="BackOff", message="Back-off restarting failed container"),
        ],
        "logs": LogEntry(
            container="app",
            lines=[
                "2026-07-24T09:00:01Z ERROR: connection refused to database at db.internal:5432",
                "2026-07-24T09:00:01Z FATAL: application startup failed",
            ],
        ),
    },
    "failed_scheduling": {
        "pod": PodStatus(
            status="Pending",
            restart_count=0,
            containers=[{"name": "app", "state": "Waiting", "reason": "Pending"}],
        ),
        "events": [
            Event(
                reason="FailedScheduling",
                message="0/3 nodes are available: 3 Insufficient memory.",
            ),
        ],
        "logs": LogEntry(container="app", lines=[]),
    },
}


class KubernetesClient:
    """Kubernetes API client with mock and real implementations.

    Usage::

        # Mock mode (default): predefined scenarios based on pod name suffix
        k8s = KubernetesClient(mode="mock")

        # Real mode: auto-detect config or use explicit kubeconfig
        k8s = KubernetesClient(mode="real")                                # auto
        k8s = KubernetesClient(mode="real", kubeconfig="/path/to/config")  # explicit
        k8s = KubernetesClient(mode="real", context="prod-cluster")        # specific context
    """

    def __init__(
        self,
        mode: str = "mock",
        kubeconfig: str | None = None,
        context: str | None = None,
    ):
        self._mode = mode
        self._configured = False

        if mode == "real":
            self._load_config(kubeconfig=kubeconfig, context=context)
            self._configured = True

    # ---- Config loading ----

    def _load_config(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> None:
        """Auto-detect K8s config from highest to lowest priority.

        1. Explicit kubeconfig path (development / cross-machine)
        2. In-cluster config (containerised deployment — /var/run/secrets/...)
        3. Default kubeconfig (~/.kube/config or KUBECONFIG env var)
        """
        if kubeconfig:
            logger.info("Loading kubeconfig from explicit path: %s", kubeconfig)
            config.load_kube_config(config_file=kubeconfig, context=context)
            return

        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
            return
        except config.ConfigException:
            logger.debug("Not running in-cluster, trying default kubeconfig")

        config.load_kube_config(context=context)
        logger.info("Loaded default kubeconfig (%s)", context or "current-context")

    # ---- Public API ----

    async def get_pod(
        self, cluster: str, namespace: str, pod: str
    ) -> dict[str, Any]:
        """Get Pod current status. Per tool-design.md §7.1."""
        if self._mode == "mock":
            return self._get_pod_mock(pod)
        return await self._get_pod_real(namespace, pod)

    async def get_events(
        self, cluster: str, namespace: str, resource: str
    ) -> dict[str, Any]:
        """Get Kubernetes Events. Per tool-design.md §7.2."""
        if self._mode == "mock":
            return self._get_events_mock(resource)
        return await self._get_events_real(namespace, resource)

    async def get_logs(
        self,
        cluster: str,
        namespace: str,
        pod: str,
        container: str,
        tail: int = 200,
    ) -> dict[str, Any]:
        """Get container logs. Per tool-design.md §7.3."""
        if self._mode == "mock":
            return self._get_logs_mock(pod, container, tail)
        return await self._get_logs_real(namespace, pod, container, tail)

    async def get_metrics(
        self, cluster: str, namespace: str, pod: str
    ) -> dict[str, Any]:
        """Get container resource metrics (Prometheus-backed in the future).

        Only pods whose containers actually ran have metrics. Pending /
        ImagePullBackOff / FailedMount pods return a structured NotAvailable error.
        """
        if self._mode == "mock":
            return self._get_metrics_mock(pod)
        return await self._get_metrics_real(namespace, pod)

    async def list_pods(
        self,
        cluster: str,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        """List pods in a namespace with a compact status summary.

        Used by the agent to discover/verify the target pod when the user's
        description is ambiguous.
        """
        if self._mode == "mock":
            return self._get_list_pods_mock(namespace)
        return await self._list_pods_real(namespace, label_selector)

    # ---- Mock implementations ----

    def _pick_scenario(self, pod_name: str) -> str:
        """Determine mock scenario from pod name suffix."""
        _POD_ALIASES: dict[str, str] = {
            "sched_fail": "failed_scheduling",
            "sched-fail": "failed_scheduling",
            "sched_failpod": "failed_scheduling",
            "sched-failpod": "failed_scheduling",
            "image_pull": "image_pull",
            "image-pull": "image_pull",
            "config_error": "config_error",
            "config-error": "config_error",
            "app_error": "app_error",
            "app-error": "app_error",
            "oom": "oom",
        }
        lower = pod_name.lower().replace("-", "_")
        if lower in _POD_ALIASES:
            return _POD_ALIASES[lower]
        for key in MOCK_SCENARIOS:
            if key in lower:
                return key
        return "app_error"

    def _get_pod_mock(self, pod: str) -> dict[str, Any]:
        scenario = self._pick_scenario(pod)
        p = MOCK_SCENARIOS[scenario]["pod"]
        return {
            "result": "success",
            "data": {
                "status": p.status,
                "restart_count": p.restart_count,
                "containers": p.containers,
                "exit_code": p.exit_code,
                "reason": p.reason,
            },
        }

    def _get_events_mock(self, resource: str) -> dict[str, Any]:
        pod_name = resource.replace("pod/", "")
        scenario = self._pick_scenario(pod_name)
        events = MOCK_SCENARIOS[scenario]["events"]
        return {
            "result": "success",
            "data": {
                "events": [
                    {"reason": e.reason, "message": e.message, "type": e.type}
                    for e in events
                ]
            },
        }

    def _get_logs_mock(
        self, pod: str, container: str, tail: int
    ) -> dict[str, Any]:
        scenario = self._pick_scenario(pod)
        logs = MOCK_SCENARIOS[scenario]["logs"]
        return {
            "result": "success",
            "data": {
                "container": container,
                "logs": logs.lines[-tail:],
            },
        }

    # Mock pod inventory so the agent can discover targets in mock mode too.
    _MOCK_POD_INVENTORY: list[tuple[str, str]] = [
        ("nginx-oom", "oom"),
        ("nginx-image-pull", "image_pull"),
        ("test-config-error", "config_error"),
        ("test-sched-fail", "failed_scheduling"),
        ("sched-failpod", "failed_scheduling"),
        ("nginx-app", "app_error"),
    ]

    def _get_metrics_mock(self, pod: str) -> dict[str, Any]:
        scenario = self._pick_scenario(pod)
        # Only scenarios where the container actually ran expose metrics.
        if scenario == "oom":
            data = {
                "metric": "container_memory_usage_bytes",
                "usage": "512Mi / 512Mi (100%)",
                "limit": "512Mi",
            }
            return {"result": "success", "data": data}
        if scenario == "app_error":
            data = {
                "metric": "container_memory_usage_bytes",
                "usage": "210Mi / 512Mi (41%)",
                "limit": "512Mi",
            }
            return {"result": "success", "data": data}
        return {
            "result": "error",
            "error_type": "NotAvailable",
            "message": f"Pod '{pod}' 的容器未运行（Pending/镜像拉取失败/挂载失败），无资源指标",
        }

    def _get_list_pods_mock(self, namespace: str) -> dict[str, Any]:
        if namespace and namespace != "default":
            return {
                "result": "success",
                "data": {"namespace": namespace, "pods": []},
            }
        pods = []
        for pod_name, scenario in self._MOCK_POD_INVENTORY:
            p = MOCK_SCENARIOS[scenario]["pod"]
            container_reason = (
                p.containers[0].get("reason") if p.containers else None
            )
            pods.append({
                "name": pod_name,
                "namespace": "default",
                "phase": p.status,
                "restart_count": p.restart_count,
                "reason": p.reason or container_reason,
            })
        return {
            "result": "success",
            "data": {"namespace": "default", "pods": pods},
        }

    # ---- Real implementations ----

    async def _get_pod_real(
        self, namespace: str, pod: str
    ) -> dict[str, Any]:
        """Fetch Pod status from live K8s API."""
        try:
            v1 = k8s_client.CoreV1Api()
            pod_obj = await asyncio.to_thread(
                v1.read_namespaced_pod, name=pod, namespace=namespace
            )
        except k8s_client.ApiException as e:
            return self._api_error(e, f"Pod {namespace}/{pod}")

        # Phase
        phase = pod_obj.status.phase or "Unknown"

        # Container states & exit code & reason
        containers = []
        exit_code: int | None = None
        reason: str | None = None

        for cs in pod_obj.status.container_statuses or []:
            info: dict[str, str] = {"name": cs.name}

            if cs.state.waiting:
                info["state"] = "Waiting"
                info["reason"] = cs.state.waiting.reason or "Unknown"
                if reason is None:
                    reason = cs.state.waiting.reason
            elif cs.state.running:
                info["state"] = "Running"
            elif cs.state.terminated:
                info["state"] = "Terminated"
                info["reason"] = cs.state.terminated.reason or "Terminated"
                if exit_code is None:
                    exit_code = cs.state.terminated.exit_code
                if cs.state.terminated.reason == "OOMKilled" and reason is None:
                    reason = "OOMKilled"

            # --- Capture lastState for CrashLoopBackOff pods ---
            # When a container is in CrashLoopBackOff, current state is "Waiting"
            # but the previous termination info lives in lastState.terminated.
            if cs.last_state and cs.last_state.terminated:
                terminated = cs.last_state.terminated
                info["last_state"] = "Terminated"
                info["last_reason"] = terminated.reason or "Terminated"
                info["last_exit_code"] = terminated.exit_code
                info["last_message"] = (terminated.message or "")[:500]
                # Exit code from lastState takes precedence
                if exit_code is None:
                    exit_code = terminated.exit_code
                if terminated.reason == "OOMKilled" and reason != "OOMKilled":
                    reason = "OOMKilled"

            containers.append(info)

        restart_count = sum(
            cs.restart_count for cs in (pod_obj.status.container_statuses or [])
        )

        return {
            "result": "success",
            "data": {
                "status": phase,
                "restart_count": restart_count,
                "containers": containers,
                "exit_code": exit_code,
                "reason": reason,
            },
        }

    async def _get_events_real(
        self, namespace: str, resource: str
    ) -> dict[str, Any]:
        """Fetch K8s Events from live API."""
        try:
            # Parse "pod/nginx-oom" → involvedObject.name=nginx-oom
            if "/" in resource:
                _, name = resource.split("/", 1)
            else:
                name = resource

            v1 = k8s_client.CoreV1Api()
            events = await asyncio.to_thread(
                v1.list_namespaced_event,
                namespace=namespace,
                field_selector=f"involvedObject.name={name},involvedObject.kind=Pod",
            )
        except k8s_client.ApiException as e:
            return self._api_error(e, f"Events for {namespace}/{resource}")

        event_list = []
        for ev in sorted(
            events.items,
            key=lambda e: e.last_timestamp or datetime.min.replace(tzinfo=timezone.utc),
        ):
            event_list.append({
                "reason": ev.reason or "",
                "message": ev.message or "",
                "type": ev.type or "Normal",
                "timestamp": str(ev.last_timestamp) if ev.last_timestamp else "",
            })

        return {
            "result": "success",
            "data": {"events": event_list},
        }

    async def _get_logs_real(
        self,
        namespace: str,
        pod: str,
        container: str,
        tail: int = 200,
    ) -> dict[str, Any]:
        """Fetch container logs from live K8s API."""
        try:
            v1 = k8s_client.CoreV1Api()
            log_text = await asyncio.to_thread(
                v1.read_namespaced_pod_log,
                name=pod,
                namespace=namespace,
                container=container,
                tail_lines=tail,
                timestamps=False,
            )
        except k8s_client.ApiException as e:
            return self._api_error(e, f"Logs for {namespace}/{pod}/{container}")

        lines = log_text.strip().split("\n") if log_text and log_text.strip() else []

        return {
            "result": "success",
            "data": {
                "container": container,
                "logs": lines[-tail:],
            },
        }

    async def _get_metrics_real(
        self, namespace: str, pod: str
    ) -> dict[str, Any]:
        """Real metrics require a Prometheus source, which is not wired yet.

        Returns a structured NotAvailable error so the agent knows to look
        elsewhere instead of guessing.
        """
        return {
            "result": "error",
            "error_type": "NotAvailable",
            "message": f"Prometheus metrics source not configured — cannot fetch metrics for {namespace}/{pod}",
        }

    async def _list_pods_real(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
    ) -> dict[str, Any]:
        """List pods from the live K8s API with a compact status summary."""
        try:
            v1 = k8s_client.CoreV1Api()
            pods = await asyncio.to_thread(
                v1.list_namespaced_pod,
                namespace=namespace,
                label_selector=label_selector,
            )
        except k8s_client.ApiException as e:
            return self._api_error(e, f"Pods in {namespace}")

        items = []
        for p in pods.items:
            status = p.status
            reason: str | None = None
            restart_count = 0
            for cs in status.container_statuses or []:
                restart_count += cs.restart_count
                if cs.state.waiting and reason is None:
                    reason = cs.state.waiting.reason
            items.append({
                "name": p.metadata.name,
                "namespace": namespace,
                "phase": status.phase or "Unknown",
                "restart_count": restart_count,
                "reason": reason,
            })

        return {
            "result": "success",
            "data": {"namespace": namespace, "pods": items},
        }

    # ---- Error handling ----

    @staticmethod
    def _api_error(exc: k8s_client.ApiException, target: str) -> dict[str, Any]:
        """Map k8s API exceptions to structured error responses."""
        if exc.status == 404:
            return {
                "result": "error",
                "error_type": "NotFound",
                "message": f"{target} not found",
            }
        if exc.status == 403:
            return {
                "result": "error",
                "error_type": "Forbidden",
                "message": f"Permission denied accessing {target}: {exc.reason}",
            }
        if exc.status == 401:
            return {
                "result": "error",
                "error_type": "Unauthorized",
                "message": f"Authentication failed for {target}: check kubeconfig credentials",
            }
        return {
            "result": "error",
            "error_type": "ApiError",
            "message": f"K8s API error ({exc.status}) for {target}: {exc.reason}",
        }
