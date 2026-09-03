"""Concrete K8s investigation tools — thin wrappers over the KubernetesClient adapter.

Each handler accepts ONLY schema-declared kwargs (no cluster/context plumbing —
the adapter owns connection details). Handlers return the adapter's raw dict so
the caller can convert it into Evidence.

Registered via :func:`build_k8s_tool_registry`. Used by the P1 ReAct loop.
"""

import os
from typing import Any

from infrastructure_agent.adapters.k8s_client import KubernetesClient
from infrastructure_agent.tools.registry import ToolRegistry, ToolSpec

_k8s = KubernetesClient(mode=os.getenv("K8S_MODE", "mock"))
_CLUSTER = "prod"  # ignored in mock mode; real mode uses the kubeconfig context

DEFAULT_TAIL = 50
MAX_TAIL = 200


# ---- Handlers ----

def _first_container_name(raw: dict) -> str | None:
    """Pull the first container name out of a get_pod raw response."""
    try:
        containers = raw["data"]["containers"]
        if containers:
            return containers[0].get("name") or "app"
    except (KeyError, TypeError):
        pass
    return None


async def get_pod_status(namespace: str = "default", pod: str = "") -> dict:
    """Get Pod current status (phase, restart count, container states,
    exit code incl. lastState for CrashLoopBackOff)."""
    return await _k8s.get_pod(cluster=_CLUSTER, namespace=namespace, pod=pod)


async def list_pod_events(namespace: str = "default", pod: str = "") -> dict:
    """Get Kubernetes events for one pod (reason/message/type), newest last."""
    return await _k8s.get_events(cluster=_CLUSTER, namespace=namespace, resource=pod)


async def get_container_logs(
    namespace: str = "default",
    pod: str = "",
    container: str | None = None,
    tail: int = DEFAULT_TAIL,
) -> dict:
    """Get container logs. When container is omitted the first container of the
    pod is used automatically."""
    if not container:
        pod_raw = await _k8s.get_pod(cluster=_CLUSTER, namespace=namespace, pod=pod)
        container = _first_container_name(pod_raw) or "app"
    return await _k8s.get_logs(
        cluster=_CLUSTER,
        namespace=namespace,
        pod=pod,
        container=container,
        tail=min(max(int(tail), 1), MAX_TAIL),
    )


async def get_pod_metrics(namespace: str = "default", pod: str = "") -> dict:
    """Get container memory usage vs limit. Only pods whose containers ran
    have metrics — others return a NotAvailable error."""
    return await _k8s.get_metrics(cluster=_CLUSTER, namespace=namespace, pod=pod)


async def list_pods(namespace: str, label_selector: str | None = None) -> dict:
    """List pods in a namespace with phase/restart/reason summary. Use to
    discover or verify which pod matches the user's description."""
    return await _k8s.list_pods(
        cluster=_CLUSTER, namespace=namespace, label_selector=label_selector
    )


# ---- Tool specs ----

def _schema(**properties) -> dict:
    required = [name for name, meta in properties.items() if meta.get("__required__")]
    cleaned = {}
    for name, meta in properties.items():
        cleaned[name] = {k: v for k, v in meta.items() if k != "__required__"}
    schema: dict = {"type": "object", "properties": cleaned}
    if required:
        schema["required"] = required
    return schema


def _param(desc: str, required: bool = False, default: Any = None) -> dict:
    meta: dict[str, Any] = {
        "type": "string",
        "description": desc,
        "__required__": required,
    }
    if default is not None:
        meta["default"] = default
    return meta


K8S_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="get_pod_status",
        description=(
            "查看 Pod 的当前状态：Phase、重启次数、容器状态、退出码"
            "（CrashLoopBackOff 时包含 lastState 的上次退出码与 reason）。"
            "调查任何 Pod 故障的第一步。"
        ),
        parameters_schema=_schema(
            namespace=_param("Pod 所在命名空间，默认 default", default="default"),
            pod=_param("要检查的 Pod 名称", required=True),
        ),
        handler=get_pod_status,
    ),
    ToolSpec(
        name="list_pod_events",
        description=(
            "列出某个 Pod 的 Kubernetes Events（reason/message/type）。"
            "K8s 把真实故障类型放在 message 里，例如 FailedScheduling、"
            "ImagePullBackOff、OOMKilled、FailedMount 都会出现在这里。"
            "Pod 处于 Pending 或反复重启时用它定位原因。"
        ),
        parameters_schema=_schema(
            namespace=_param("Pod 所在命名空间，默认 default", default="default"),
            pod=_param("要查 Events 的 Pod 名称", required=True),
        ),
        handler=list_pod_events,
    ),
    ToolSpec(
        name="get_container_logs",
        description=(
            "获取容器应用日志（默认尾部 50 行）。容器已启动但崩溃（如"
            " CrashLoopBackOff 且退出码非 137）时查它找应用层错误。"
            "若容器从未启动（Pending/ImagePullBackOff/挂载失败）日志为空，无需调用。"
        ),
        parameters_schema=_schema(
            namespace=_param("Pod 所在命名空间，默认 default", default="default"),
            pod=_param("要查日志的 Pod 名称", required=True),
            container=_param("容器名，省略时自动取 Pod 第一个容器", default=None),
            tail=_param("返回日志行数（1-200），默认 50", default=DEFAULT_TAIL),
        ),
        handler=get_container_logs,
    ),
    ToolSpec(
        name="get_pod_metrics",
        description=(
            "获取容器内存用量 vs Limit。用于确认是否 OOM：usage 接近/达到"
            " 100% 且容器退出码 137 时可确认内存不足。容器未运行的 Pod"
            "（Pending/拉取失败/挂载失败）会返回 NotAvailable。"
        ),
        parameters_schema=_schema(
            namespace=_param("Pod 所在命名空间，默认 default", default="default"),
            pod=_param("要查指标的 Pod 名称", required=True),
        ),
        handler=get_pod_metrics,
    ),
    ToolSpec(
        name="list_pods",
        description=(
            "列出命名空间下所有 Pod（Phase/重启次数/原因）。当用户描述的 Pod"
            " 名不确定、或需要对比同命名空间其他 Pod 状态时使用。仅作导航，"
            "不产生诊断证据。"
        ),
        parameters_schema=_schema(
            namespace=_param("命名空间，默认 default", default="default"),
            label_selector=_param("可选 label 选择器，如 app=nginx", default=None),
        ),
        handler=list_pods,
        produces_evidence=False,
    ),
]


def build_k8s_tool_registry() -> ToolRegistry:
    """Create a registry pre-loaded with the K8s investigation tools."""
    registry = ToolRegistry()
    registry.register_many(K8S_TOOL_SPECS)
    return registry


# Module-level singleton so memoization survives across calls within one process.
tool_registry = build_k8s_tool_registry()
