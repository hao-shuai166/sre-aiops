"""Target resolution — parse the investigation target from user input.

Single source of truth for turning a natural-language question into a
concrete investigation target (cluster / namespace / pod). Previously the
parse helpers lived in workflow/pod_crash_workflow.py and the cluster name
was hardcoded as ``"prod"`` in two places — now every workflow resolves its
target through :func:`resolve_target`.

Cluster handling: the adapter treats ``cluster`` as a response label only
(connection details are owned by KubernetesClient mode/context). The label
comes from the ``K8S_CLUSTER`` env var (default ``prod``) so deployments can
identify themselves without code changes.
"""

import os
import re
from dataclasses import dataclass


@dataclass
class TargetContext:
    """Concrete investigation target resolved from user input.

    A HINT for the ReAct loop (pre-filled tool args), not a constraint —
    the LLM may investigate other pods/namespaces it discovers.
    """

    cluster: str
    namespace: str
    pod: str


def default_cluster() -> str:
    """Cluster label from env; the adapter ignores it in mock mode and uses
    its own kubeconfig context in real mode."""
    return os.getenv("K8S_CLUSTER", "prod")


# 不应被当作 pod 名的状态词 / 命名空间词 / 结构词
_STATUS_WORDS = {
    "pending", "running", "succeeded", "failed", "unknown", "terminating",
    "crashloopbackoff", "imagepullbackoff", "crashloop", "back-off", "backoff",
    "crash", "error", "oomkilled", "oom", "ready", "notready",
    "restart", "restarting", "restarted", "terminated", "completed", "waiting",
    "containercreating", "starting", "init", "unschedulable",
    "default", "production", "prod", "staging", "stage", "dev", "test",
    "kube-system", "monitoring", "namespace", "ns", "pod", "deployment",
    "service", "svc", "replicaset", "statefulset", "job", "cronjob", "name",
}

# K8s 标识符 token：小写字母/数字开头，可含中划线，字母/数字结尾
_POD_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*[a-z0-9]")

# 常见业务前缀，作为第二优先级兜底
_COMMON_PREFIXES = (
    "nginx", "app", "web", "api", "redis", "mysql", "postgres", "order",
    "service", "gateway", "auth", "user", "admin", "frontend", "backend",
    "sched", "test", "kube", "node", "etcd", "prometheus", "grafana",
)

_NAMESPACE_ALIASES = {
    "default": "default", "production": "production", "prod": "prod",
    "staging": "staging", "stage": "staging", "dev": "dev", "test": "test",
    "kube-system": "kube-system", "monitoring": "monitoring",
}


def parse_pod_name(user_input: str) -> str:
    """Extract pod name from user input.

    Priority:
    1. token 含中划线（K8s pod 名几乎必带 `-`，如 sched-failpod / nginx-oom）
    2. token 以已知业务前缀开头（nginx / app / web ...）
    3. fallback "nginx"
    状态词（pending / crashloopbackoff ...）与命名空间词一律排除。
    """
    tokens = _POD_TOKEN_RE.findall(user_input.lower())

    hyphen_candidates: list[str] = []
    prefix_candidates: list[str] = []
    for t in tokens:
        if t in _STATUS_WORDS:
            continue
        if "-" in t:
            hyphen_candidates.append(t)
        elif t.startswith(_COMMON_PREFIXES) and len(t) >= 2:
            prefix_candidates.append(t)

    for t in hyphen_candidates + prefix_candidates:
        return t
    return "nginx"


def parse_namespace(user_input: str) -> str:
    lower = user_input.lower()
    # 1. 显式上下文：`xxx命名空间` / `xxx namespace` / `xxx ns`
    m = re.search(r"([a-z0-9-]+)\s*(?:命名空间|namespace|ns)", lower)
    if m:
        cand = _NAMESPACE_ALIASES.get(m.group(1))
        if cand:
            return cand
    # 2. 直接匹配已知 namespace 词
    for word in lower.split():
        cleaned = re.sub(r"[^a-z0-9-]", "", word)
        if cleaned in _NAMESPACE_ALIASES:
            return _NAMESPACE_ALIASES[cleaned]
    return "default"


def resolve_target(user_input: str) -> TargetContext:
    """Resolve the full investigation target from a user question."""
    return TargetContext(
        cluster=default_cluster(),
        namespace=parse_namespace(user_input),
        pod=parse_pod_name(user_input),
    )
