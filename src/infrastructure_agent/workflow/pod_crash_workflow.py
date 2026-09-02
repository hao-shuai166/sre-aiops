"""CrashLoopBackOff Diagnosis Workflow — LangGraph StateGraph.

Per pod-crashloopbackoff.md, this workflow diagnoses Pod CrashLoopBackOff through:
1. Get Pod Status + Events (baseline evidence)
2. Diagnosis Router → 5-path branching (OOM / AppError / ImagePull / FailSched / Config)
3. Evidence Collection → Decision Node (evidence sufficient?)
4. Reasoning Loop (retry up to max_iteration) → RCA Output

All state mutation happens through AgentState. Tool calls use the mock K8s client.
Evidence is built via EvidenceBuilder before entering AgentState.evidence.
"""

import os
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from infrastructure_agent.adapters.k8s_client import KubernetesClient
from infrastructure_agent.domain.models import (
    AgentState,
    Diagnosis,
    Evidence,
    EvidenceResource,
    EvidenceSource,
    ExecutionState,
    IntentState,
    ReasoningControl,
    ReasoningStep,
    RequestContext,
)
from infrastructure_agent.llm import build_rca_prompt, get_llm_client, RCA_SYSTEM_PROMPT
from infrastructure_agent.tools.evidence_builder import EvidenceBuilder

_k8s = KubernetesClient(mode=os.getenv("K8S_MODE", "mock"))
_builder = EvidenceBuilder()


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


def _parse_pod_name(user_input: str) -> str:
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


def _parse_namespace(user_input: str) -> str:
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


def _get_container_name(state: "WorkflowState") -> str:
    """Extract the first container name from PodStatus evidence."""
    for ev in state.evidence:
        if ev.type == "PodStatus":
            containers = ev.content.get("containers", [])
            if containers:
                return containers[0].get("name", "app")
    return "app"


def _extract_image_from_events(events: list[dict]) -> str:
    """Extract the image name from Kubernetes event messages.

    Looks for patterns like: 'Pulling image "nginx:latesr"'
    or 'Failed to pull image "nginx:latesr": ...'
    Returns the image string or "unknown" if not found.
    """
    for ev in events:
        msg = ev.get("message", "")
        for prefix in ('Pulling image "', 'Failed to pull image "',
                       'Back-off pulling image "', 'Successfully pulled image "'):
            if prefix in msg:
                start = msg.index(prefix) + len(prefix)
                try:
                    end = msg.index('"', start)
                    return msg[start:end]
                except ValueError:
                    return msg[start:]
    return "unknown"


# ---- State definition ----
class WorkflowState(AgentState):
    """Extended AgentState with LangGraph message support and workflow metadata.

    Note: Field names MUST NOT start with underscore — Pydantic v2 treats
    underscore-prefixed fields as private and drops them during serialization.
    """

    messages: Annotated[list, add_messages] = []
    wf_pod: str = "nginx"
    wf_namespace: str = "default"
    wf_cluster: str = "prod"
    rca_mode: str = "unknown"


# ---- Node: Initialize ----
def init_state(state: WorkflowState) -> dict:
    """Set request context and intent from user input."""
    user_input = state.request.user_input or ""
    pod = _parse_pod_name(user_input)
    ns = _parse_namespace(user_input)

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
            current_workflow="pod_crash_diagnosis",
            current_step="get_pod",
            status="running",
            iteration=0,
        ),
        "reasoning_control": ReasoningControl(
            iteration=0,
            max_iteration=5,
            confidence=0.0,
            need_more_evidence=True,
        ),
        # Store extracted pod info in evidence content for downstream use
        "wf_pod": pod,
        "wf_namespace": ns,
        "wf_cluster": "prod",
    }


# ---- Node: Get Pod Status ----
async def get_pod_node(state: WorkflowState) -> dict:
    namespace = state.wf_namespace
    pod = state.wf_pod
    cluster = state.wf_cluster

    raw = await _k8s.get_pod(cluster=cluster, namespace=namespace, pod=pod)
    evidence = _builder.build_from_get_pod(raw, namespace=namespace, pod=pod)

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Pod status={evidence.content.get('status')}, restart_count={evidence.content.get('restart_count')}",
                conclusion="需要查看 Events 确定原因",
            )
        ],
        "execution": ExecutionState(
            current_workflow="pod_crash_diagnosis",
            current_step="get_events",
            status="running",
            iteration=state.execution.iteration,
        ),
    }


# ---- Node: Get Events ----
async def get_events_node(state: WorkflowState) -> dict:
    namespace = state.wf_namespace
    pod = state.wf_pod
    cluster = state.wf_cluster

    raw = await _k8s.get_events(
        cluster=cluster, namespace=namespace, resource=f"pod/{pod}"
    )
    evidence = _builder.build_from_get_events(raw, namespace=namespace, pod=pod)

    events = evidence.content.get("events", [])
    event_reasons = [e.get("reason", "unknown") for e in events]

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Events: {event_reasons}",
                conclusion="根据 Event 类型选择诊断路径",
            )
        ],
        "execution": ExecutionState(
            current_workflow="pod_crash_diagnosis",
            current_step="diagnosis_router",
            status="running",
            iteration=state.execution.iteration,
        ),
    }


# ---- Diagnosis Router ----
# Map from routing key → human-readable problem name for diagnosis output
_EVENT_ROUTE_PROBLEM_MAP = {
    "oom": "Pod OOMKilled",
    "image_pull": "Pod ImagePullBackOff",
    "failed_scheduling": "Pod FailedScheduling",
    "config_error": "Pod ConfigError",
    "app_error": "Pod CrashLoopBackOff",
}


def classify_event(state: WorkflowState) -> str:
    """Classify the event reason to determine the diagnosis path.

    Priority of signal sources:
    1. PodStatus — container waiting reason is the most authoritative
       (e.g. reason="ImagePullBackOff", "CrashLoopBackOff", "CreateContainerConfigError")
    2. KubernetesEvent — reason + message fields combined for keyword matching
       (K8s sometimes reports generic reason="Failed" with the real cause in message)
    """
    if not state.evidence:
        return "app_error"

    # ---- 1. Check PodStatus evidence (most reliable) ----
    for ev in reversed(state.evidence):
        if ev.type == "PodStatus":
            # Pod-level reason
            pod_reason = (ev.content.get("reason") or "").lower()
            # Container-level waiting reason
            for c in ev.content.get("containers", []):
                c_reason = (c.get("reason") or "").lower()
                combined = f"{pod_reason} {c_reason}"

                if "imagepullbackoff" in combined or "errimagepull" in combined:
                    return "image_pull"
                if "oomkilled" in combined:
                    return "oom"
                if "createcontainerconfigerror" in combined or "configerror" in combined:
                    return "config_error"
                if "crashloopbackoff" in combined:
                    # CrashLoopBackOff is a state, not a root cause.
                    # Still need events/logs to determine the actual path.
                    break

    # ---- 2. Check KubernetesEvent evidence (fallback / supplementary) ----
    for ev in reversed(state.evidence):
        if ev.type == "KubernetesEvent":
            events = ev.content.get("events", [])
            # Combine reason + message for keyword matching
            candidates = []
            for e in events:
                candidates.append(e.get("reason", ""))
                candidates.append(e.get("message", ""))
            search_str = " ".join(candidates).lower()

            if "oomkilled" in search_str:
                return "oom"
            if "imagepullbackoff" in search_str or "errimagepull" in search_str:
                return "image_pull"
            if "back-off pulling image" in search_str or "failed to pull image" in search_str:
                return "image_pull"
            if "failedscheduling" in search_str:
                return "failed_scheduling"
            if "failedmount" in search_str or "configmap" in search_str or "secret" in search_str:
                return "config_error"

    return "app_error"


def _diagnosis_problem(state: WorkflowState) -> str:
    """Derive the human-readable problem name from the classified route.
    
    After classify_event() has run in get_events → router, the evidence
    contains enough information to re-derive the same classification.
    """
    route = classify_event(state)
    return _EVENT_ROUTE_PROBLEM_MAP.get(route, "Pod Failure")


# ---- OOM Path ----
async def query_memory_metrics(state: WorkflowState) -> dict:
    """Simulate querying Prometheus for memory metrics."""
    namespace = state.wf_namespace
    pod = state.wf_pod

    data = {
        "result": "success",
        "data": {
            "metric": "container_memory_usage_bytes",
            "usage": "512Mi / 512Mi (100%)",
            "limit": "512Mi",
        },
    }

    evidence = Evidence(
        id=_builder._next_id(),
        type="Metric",
        source=EvidenceSource(system="prometheus", api="query_range"),
        timestamp=datetime.now(timezone.utc),
        resource=EvidenceResource(namespace=namespace, pod=pod),
        content=data["data"],
        confidence=0.90,
    )

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Memory usage={data['data']['usage']}",
                conclusion="Memory Limit 不足导致 OOMKilled" if "100%" in data["data"]["usage"] else "Memory 使用正常，需进一步排查",
            )
        ],
    }


# ---- App Error Path ----
async def query_app_logs(state: WorkflowState) -> dict:
    """Get container logs for application error analysis."""
    namespace = state.wf_namespace
    pod = state.wf_pod
    cluster = state.wf_cluster
    container = _get_container_name(state)

    raw = await _k8s.get_logs(
        cluster=cluster, namespace=namespace, pod=pod, container=container, tail=200
    )
    evidence = _builder.build_from_get_logs(
        raw, namespace=namespace, pod=pod, container=container
    )

    logs = evidence.content.get("logs", [])
    has_error = any("error" in l.lower() or "fatal" in l.lower() for l in logs)

    # Empty logs + CrashLoopBackOff = container exits before producing any output
    if not logs:
        log_observation = "容器无任何日志输出（可能启动即退出，entrypoint 或二进制异常）"
        log_conclusion = "容器启动即崩溃，未产生日志——需检查 entrypoint、启动命令及 exit_code"
    elif has_error:
        log_observation = f"Container logs (first 3): {logs[:3]}"
        log_conclusion = "应用启动异常，日志显示应用层错误"
    else:
        log_observation = f"Container logs (first 3): {logs[:3]}"
        log_conclusion = "日志中无明显错误，需结合 exit_code 进一步分析"

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=log_observation,
                conclusion=log_conclusion,
            )
        ],
    }


# ---- ImagePullBackOff Path ----
async def check_image_pull(state: WorkflowState) -> dict:
    """Check pod status for image pull issues."""
    namespace = state.wf_namespace
    pod = state.wf_pod
    cluster = state.wf_cluster

    raw = await _k8s.get_pod(cluster=cluster, namespace=namespace, pod=pod)
    evidence = _builder.build_from_get_pod(raw, namespace=namespace, pod=pod)

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Pod status={evidence.content.get('status')}, reason={evidence.content.get('reason')}",
                conclusion="镜像拉取失败，检查镜像名称、tag 和 registry 访问权限",
            )
        ],
    }


# ---- FailedScheduling Path ----
async def check_scheduling(state: WorkflowState) -> dict:
    """Check cluster node resources for scheduling issues."""
    return {
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation="Schedule failure detected",
                conclusion="集群节点资源不足，需要扩容或清理资源",
            )
        ],
    }


# ---- Config Error Path ----
async def check_config(state: WorkflowState) -> dict:
    """Check configuration-related issues."""
    namespace = state.wf_namespace
    pod = state.wf_pod
    cluster = state.wf_cluster

    raw = await _k8s.get_pod(cluster=cluster, namespace=namespace, pod=pod)
    evidence = _builder.build_from_get_pod(raw, namespace=namespace, pod=pod)

    events = state.evidence[-1].content.get("events", []) if state.evidence else []
    event_msgs = [e.get("message", "") for e in events]

    return {
        "evidence": state.evidence + [evidence],
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"Config error: {event_msgs}",
                conclusion="ConfigMap/Secret 缺失或挂载失败，检查资源配置",
            )
        ],
    }


# ---- Decision Node (function node: updates reasoning_control) ----
def decision_node(state: WorkflowState) -> dict:
    """Update reasoning control state and estimate confidence.

    Adjusts confidence based on the diagnosed route — some paths
    (image_pull, failed_scheduling, config_error) don't benefit
    from looping because they have no additional evidence to collect.
    """
    ctrl = state.reasoning_control
    evidence_types = {ev.type for ev in state.evidence}

    estimated_confidence = 0.0
    if "PodStatus" in evidence_types and "KubernetesEvent" in evidence_types:
        estimated_confidence += 0.5
    if "Metric" in evidence_types:
        estimated_confidence += 0.3
    if "ContainerLog" in evidence_types:
        estimated_confidence += 0.2

    # Routes that don't need extra evidence collection get a boost
    # (no metrics/logs to collect for these failure modes)
    route = classify_event(state)
    if route in ("image_pull", "failed_scheduling", "config_error"):
        estimated_confidence = max(estimated_confidence, 0.80)

    new_iteration = ctrl.iteration + 1
    return {
        "reasoning_control": ReasoningControl(
            iteration=new_iteration,
            max_iteration=ctrl.max_iteration,
            confidence=estimated_confidence,
            need_more_evidence=estimated_confidence < 0.70,
        ),
    }


# ---- Route After Decision (conditional_edge: reads updated state) ----
def route_after_decision(state: WorkflowState) -> Literal["rca", "continue_loop"]:
    """Route to RCA if evidence sufficient or max iterations reached."""
    ctrl = state.reasoning_control
    if ctrl.should_stop or ctrl.confidence >= 0.7:
        return "rca"
    return "continue_loop"


# ---- RCA Node (LLM-powered with rule-based fallback) ----
def _fallback_rca(state: WorkflowState) -> dict:
    """Rule-based RCA fallback when LLM is unavailable.

    Preserves the original V1 logic: match evidence type → hardcoded root_cause template.
    """
    events_ev = None
    pod_ev = None
    log_ev = None
    metric_ev = None

    for ev in state.evidence:
        if ev.type == "KubernetesEvent":
            events_ev = ev
        elif ev.type == "PodStatus":
            pod_ev = ev
        elif ev.type == "ContainerLog":
            log_ev = ev
        elif ev.type == "Metric":
            metric_ev = ev

    root_cause = "Unknown"
    suggestion = "需要进一步排查"
    confidence = 0.5

    # Extract exit_code and restart_count from PodStatus
    pod_exit_code = None
    pod_restart_count = 0
    if pod_ev:
        pod_exit_code = pod_ev.content.get("exit_code")
        pod_restart_count = pod_ev.content.get("restart_count", 0)

    if events_ev:
        events = events_ev.content.get("events", [])
        # Join reason + message (same approach as classify_event)
        candidates = []
        for e in events:
            candidates.append(e.get("reason", ""))
            candidates.append(e.get("message", ""))
        search_str = " ".join(candidates).lower()

        if "oomkilled" in search_str:
            root_cause = "Memory Limit 不足导致 OOMKilled"
            suggestion = "增加 Pod memory limit 或优化应用内存使用"
            confidence = 0.90 if metric_ev else 0.70
        elif "failedmount" in search_str or "configmap" in search_str or "secret" in search_str:
            root_cause = "ConfigMap / Secret 配置异常"
            suggestion = "检查 ConfigMap/Secret 是否存在、权限是否正确"
            confidence = 0.80
        elif "imagepullbackoff" in search_str or "errimagepull" in search_str:
            root_cause = "镜像拉取失败"
            suggestion = "检查镜像名称、tag 和 imagePullSecrets 配置"
            confidence = 0.85
        elif "failed to pull image" in search_str:
            # message-based match: "Failed to pull image ... not found"
            # Extract the image name from the event messages for better diagnosis
            image_name = _extract_image_from_events(events)
            if "not found" in search_str:
                root_cause = f"镜像拉取失败：镜像 {image_name} 不存在"
                suggestion = f"检查镜像名称和 tag 是否正确（镜像 {image_name} 在 registry 中未找到）"
            else:
                root_cause = f"镜像拉取失败：{image_name}"
                suggestion = "检查镜像名称、tag、registry 访问权限及 imagePullSecrets 配置"
            confidence = 0.85
        elif "back-off pulling image" in search_str:
            image_name = _extract_image_from_events(events)
            root_cause = f"镜像拉取失败：{image_name}"
            suggestion = f"检查镜像名称和 tag 是否正确，registry 是否可访问"
            confidence = 0.80
        elif "failedscheduling" in search_str:
            root_cause = "集群资源不足导致调度失败"
            suggestion = "扩容集群节点或释放资源"
            confidence = 0.85
        elif log_ev:
            logs = log_ev.content.get("logs", [])
            log_text = " ".join(logs).lower()
            if "connection refused" in log_text or "database" in log_text:
                root_cause = "依赖服务不可用（数据库连接失败）"
                suggestion = "检查依赖服务状态和网络连通性"
                confidence = 0.80
            elif log_text.strip():
                # Non-empty logs without recognized patterns
                root_cause = "应用启动异常"
                suggestion = f"检查应用启动日志中的具体错误信息；exit_code={pod_exit_code}"
                confidence = 0.55
            elif not log_text.strip():
                # --- Empty logs: container exits before producing any output ---
                if pod_exit_code is not None:
                    if pod_exit_code == 137:
                        root_cause = "容器被 SIGKILL 终止（可能 OOM 或 preStop hook 超时）"
                        suggestion = "检查 memory limit 配置及 liveness probe 参数"
                        confidence = 0.65
                    elif pod_exit_code == 1:
                        root_cause = "应用进程异常退出（exit_code=1），容器启动即崩溃，无日志输出"
                        suggestion = "1. 检查容器 entrypoint/CMD 是否正确；2. 验证二进制文件路径及执行权限；3. 在本地用相同镜像启动排查"
                        confidence = 0.60
                    elif pod_exit_code == 126:
                        root_cause = "容器 entrypoint 无执行权限（exit_code=126）"
                        suggestion = "检查 Dockerfile 中 CMD/ENTRYPOINT 指定的文件是否为可执行文件"
                        confidence = 0.75
                    elif pod_exit_code == 127:
                        root_cause = "容器 entrypoint 命令未找到（exit_code=127）"
                        suggestion = "检查 Dockerfile 中的 CMD/ENTRYPOINT 路径是否正确，二进制文件是否存在"
                        confidence = 0.75
                    else:
                        root_cause = f"容器启动后立即退出（exit_code={pod_exit_code}），无日志输出"
                        suggestion = f"exit_code={pod_exit_code} 指示应用启动失败；检查 entrypoint、环境变量及依赖文件"
                        confidence = 0.55
                else:
                    root_cause = "容器启动即崩溃，无日志输出，且未捕获到退出码"
                    suggestion = "1. 检查 Pod lastState 获取退出码；2. 验证 entrypoint/CMD 是否正确；3. 尝试 kubectl describe pod 查看详细状态"
                    confidence = 0.35

    evidence_ids = [ev.id for ev in state.evidence]
    problem = _diagnosis_problem(state)

    return {
        "diagnosis": Diagnosis(
            problem=problem,
            root_cause=root_cause,
            evidence=evidence_ids,
            suggestion=suggestion,
            confidence=confidence,
        ),
        "execution": ExecutionState(
            current_workflow="pod_crash_diagnosis",
            current_step="rca_output",
            status="completed",
            iteration=state.execution.iteration,
        ),
        "reasoning": state.reasoning + [
            ReasoningStep(
                step=len(state.reasoning) + 1,
                observation=f"All evidence collected: {evidence_ids}",
                conclusion=f"Root Cause: {root_cause}",
            )
        ],
        "rca_mode": "rule",  # mark that fallback was used
    }


async def rca_node(state: WorkflowState) -> dict:
    """Generate the final root cause analysis via LLM, falling back to rules.

    Priority:
    1. LLM-powered RCA (if API key configured and accessible)
    2. Rule-based fallback (preserved from V1)
    """
    evidence_ids = [ev.id for ev in state.evidence]
    problem = _diagnosis_problem(state)
    user_input = state.request.user_input or problem

    # ---- Attempt LLM-based RCA ----
    llm = get_llm_client()
    if llm.available:
        prompt = build_rca_prompt(
            evidence_list=state.evidence,
            reasoning_steps=state.reasoning,
            user_input=user_input,
        )
        llm_result = await llm.generate_structured(
            system_prompt=RCA_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.3,
            max_tokens=1500,
        )

        if llm_result and isinstance(llm_result, dict):
            # Validate required fields
            problem_name = str(llm_result.get("problem", problem))
            root_cause = str(llm_result.get("root_cause", "Unknown"))
            suggestion = str(llm_result.get("suggestion", "需要进一步排查"))
            confidence = float(llm_result.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            return {
                "diagnosis": Diagnosis(
                    problem=problem_name,
                    root_cause=root_cause,
                    evidence=evidence_ids,
                    suggestion=suggestion,
                    confidence=confidence,
                ),
                "execution": ExecutionState(
                    current_workflow="pod_crash_diagnosis",
                    current_step="rca_output",
                    status="completed",
                    iteration=state.execution.iteration,
                ),
                "reasoning": state.reasoning + [
                    ReasoningStep(
                        step=len(state.reasoning) + 1,
                        observation=f"LLM analyzed {len(state.evidence)} evidence items: {evidence_ids}",
                        conclusion=root_cause,
                    )
                ],
                "rca_mode": "llm",
            }

    # ---- Fallback: rule-based RCA ----
    result = _fallback_rca(state)
    result["rca_mode"] = "rule"
    return result


# ---- Continue Node ----
def continue_node(state: WorkflowState) -> dict:
    """Increment iteration counter and route back for more evidence."""
    ctrl = state.reasoning_control
    return {
        "reasoning_control": ReasoningControl(
            iteration=ctrl.iteration + 1,
            max_iteration=ctrl.max_iteration,
            confidence=ctrl.confidence,
            need_more_evidence=True,
        ),
        "execution": ExecutionState(
            current_workflow="pod_crash_diagnosis",
            current_step="collect_more_evidence",
            status="running",
            iteration=ctrl.iteration + 1,
        ),
    }


# ---- Build Graph ----
def build_crashloop_graph() -> StateGraph:
    """Construct the CrashLoopBackOff diagnosis LangGraph StateGraph."""

    graph = StateGraph(WorkflowState)

    # Register function nodes
    graph.add_node("init", init_state)
    graph.add_node("get_pod", get_pod_node)
    graph.add_node("get_events", get_events_node)

    # Diagnosis paths
    graph.add_node("query_memory_metrics", query_memory_metrics)
    graph.add_node("query_app_logs", query_app_logs)
    graph.add_node("check_image_pull", check_image_pull)
    graph.add_node("check_scheduling", check_scheduling)
    graph.add_node("check_config", check_config)

    # Decision + RCA
    graph.add_node("decision", decision_node)
    graph.add_node("rca", rca_node)
    graph.add_node("continue_loop", continue_node)

    # Edges
    graph.set_entry_point("init")
    graph.add_edge("init", "get_pod")
    graph.add_edge("get_pod", "get_events")

    # Diagnosis Router: conditional edge based on event classification
    graph.add_conditional_edges(
        "get_events",
        classify_event,
        {
            "oom": "query_memory_metrics",
            "app_error": "query_app_logs",
            "image_pull": "check_image_pull",
            "failed_scheduling": "check_scheduling",
            "config_error": "check_config",
        },
    )

    # All diagnosis paths converge to decision
    graph.add_edge("query_memory_metrics", "decision")
    graph.add_edge("query_app_logs", "decision")
    graph.add_edge("check_image_pull", "decision")
    graph.add_edge("check_scheduling", "decision")
    graph.add_edge("check_config", "decision")

    # Decision: update reasoning_control, then route
    graph.add_conditional_edges(
        "decision",
        route_after_decision,
        {
            "rca": "rca",
            "continue_loop": "continue_loop",
        },
    )

    # Continue loop: route back based on previous path
    graph.add_conditional_edges(
        "continue_loop",
        classify_event,
        {
            "oom": "query_memory_metrics",
            "app_error": "query_app_logs",
            "image_pull": "check_image_pull",
            "failed_scheduling": "check_scheduling",
            "config_error": "check_config",
        },
    )

    graph.add_edge("rca", END)

    return graph.compile()
