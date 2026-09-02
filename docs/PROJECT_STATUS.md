# Infrastructure Agent — 项目状态报告

> 更新时间：2026-07-24  
> 版本：V1 Sprint 1 完成

---

## 1. 项目概述

Infrastructure Agent 是面向企业级 SRE 场景的 AI 运维智能体平台。核心目标：将 SRE 故障排查经验通过 **LLM + Workflow + MCP Tool + Observability 数据** 进行结构化沉淀，使 AI 能够完成基础设施诊断、根因分析（RCA）和辅助运维。

V1 目标：实现 Kubernetes Pod 故障场景下的智能诊断。用户输入自然语言问题，Agent 自动采集证据、分析根因、输出诊断报告。

---

## 2. 已完成的工作

### 2.1 设计文档（4 份 + 1 份 ADR）

| 文档 | 路径 | 内容 | 状态 |
|------|------|------|------|
| Agent Flow | `docs/architecture/agent-flow.md` | Agent 整体流程：User → Intent → Workflow → Tool → RCA，含反馈循环设计 | 已评审通过 |
| Agent State Design | `docs/architecture/agent-state-design.md` | 6 模块 AgentState 共享上下文设计：Request / Intent / Execution / Evidence / Reasoning / Diagnosis | 已评审通过 |
| Tool Design | `docs/architecture/tool-design.md` | 7 个 MCP Tool 定义（get_pod / get_events / get_logs / query_logs / query_metrics / get_trace / get_alert），含 7 维 Evidence 结构和 EvidenceBuilder 规范 | 已评审通过 |
| CrashLoopBackOff Workflow | `docs/workflow/pod-crashloopbackoff.md` | Pod 崩溃诊断工作流设计：5 条诊断路径（OOM / AppError / ImagePull / FailedScheduling / ConfigError）、Router 逻辑、State 转移、LangGraph 节点标注 | 已评审通过 |
| ADR-0001 | `docs/adr/ADR-0001.md` | 架构决策：选择 LangGraph 作为 Workflow 引擎、MCP 作为 Tool 协议、Pydantic v2 作为数据模型 | 已归档 |

设计文档评审过程中修复的跨文档一致性问题：
- agent-flow.md §3 架构图：State 从线性节点改为底部共享上下文
- agent-flow.md §8：删除内联 State 定义，指向 agent-state-design.md
- tool-design.md：7 个 Tool 全部补齐 7 维 Evidence 结构；错误模型 `status` → `result`；K8s get_logs vs Loki query_logs 边界明确
- pod-crashloopbackoff.md：补齐 Tool Input 参数；新增 ImagePullBackOff + FailedScheduling + ConfigError 路由路径；max_iterations → max_iteration

### 2.2 代码实现（6 层架构，15 个 Python 文件）

采用自底向上策略，从 Domain 层到 API 层逐层实现：

```
src/infrastructure_agent/
├── __init__.py
├── main.py                          # 6. API Layer
├── domain/
│   ├── __init__.py
│   └── models.py                    # 0. Domain Layer — 10 个 Pydantic 模型
├── adapters/
│   ├── __init__.py
│   └── k8s_client.py                 # 1. Adapter Layer — K8s 客户端（Mock）
├── mcp/
│   ├── __init__.py
│   └── kubernetes_server.py         # 2. MCP Layer — 3 个 Tool 暴露
├── tools/
│   ├── __init__.py
│   └── evidence_builder.py           # 3. Tool Layer — 7 维 Evidence 转换
├── workflow/
│   ├── __init__.py
│   └── pod_crash_workflow.py         # 4. Workflow Layer — LangGraph 状态图
├── agent/
│   ├── __init__.py
│   └── diagnosis_agent.py            # 5. Agent Layer — 意图分类 + 工作流路由
└── llm/
    └── __init__.py                   # LLM 层（预留，V2 填充）
```

#### 各层实现详情

**Domain Layer（models.py）— 10 个 Pydantic 模型**

| 模型 | 用途 |
|------|------|
| `EvidenceSource` | 证据数据来源（system + api 两级标识） |
| `EvidenceResource` | 证据关联的 K8s 资源（namespace + pod + container） |
| `Evidence` | 7 维结构化证据（id / type / source / timestamp / resource / content / confidence） |
| `ReasoningStep` | 推理链单步（step + observation + conclusion） |
| `ReasoningControl` | 循环终止控制（iteration / max_iteration / confidence / need_more_evidence + should_stop 属性） |
| `RequestContext` | 用户原始请求（user_input + user + timestamp） |
| `IntentState` | 意图分类结果（domain + problem_type + confidence） |
| `ExecutionState` | 工作流执行状态（current_workflow / current_step / status / iteration） |
| `Diagnosis` | 最终诊断输出（problem + root_cause + evidence IDs + suggestion + confidence） |
| `AgentState` | 统一 Agent 状态，所有 LangGraph 节点共享读写的 side-car 上下文 |

**Adapter Layer（k8s_client.py）— Mock 实现，5 种故障场景**

| Pod 名 | 场景 | Pod 状态 | Exit Code | 关键事件 |
|--------|------|----------|-----------|----------|
| `nginx-oom` | OOMKilled | CrashLoopBackOff | 137 | OOMKilled + BackOff |
| `app-error` | 应用错误 | CrashLoopBackOff | 1 | DB connection refused |
| `image-pull` | 镜像拉取失败 | ImagePullBackOff | — | Failed (manifest unknown) |
| `config-error` | 配置异常 | CrashLoopBackOff | 1 | FailedMount (secret not found) |
| `sched-fail` | 调度失败 | Pending | — | FailedScheduling (insufficient memory) |

提供 3 个异步方法：`get_pod()` / `get_events()` / `get_logs()`。`_pick_scenario()` 根据 Pod 名后缀匹配场景（同时支持下划线和连字符）。非 mock 模式抛出 `NotImplementedError`。

**MCP Layer（kubernetes_server.py）— 3 个 MCP Tool**

通过 `mcp.server.Server` 暴露 3 个工具，与 Workflow 层解耦：

| Tool | 输入参数 | 返回 |
|------|----------|------|
| `get_pod` | cluster, namespace, pod | Pod 状态 dict |
| `get_events` | cluster, namespace, resource | Event 列表 dict |
| `get_logs` | cluster, namespace, pod, container, tail(可选) | 日志行 dict |

`call_tool()` 路由器将工具名映射到对应 handler，返回 JSON 序列化的 `TextContent`。

**Tool Layer（evidence_builder.py）— 7 维 Evidence 转换**

`EvidenceBuilder` 提供三种工具各一个专用转换方法：

| 方法 | 输入 | 输出 Evidence type | confidence |
|------|------|---------------------|------------|
| `build_from_get_pod()` | raw dict + namespace + pod | PodStatus | 0.95 |
| `build_from_get_events()` | raw dict + namespace + pod | KubernetesEvent | 0.95 |
| `build_from_get_logs()` | raw dict + namespace + pod + container | ContainerLog | 0.90 |

内部计数器自动递增 Evidence ID（ev001, ev002, ...）。`reset_counter()` 供测试使用。

**Workflow Layer（pod_crash_workflow.py）— LangGraph StateGraph，12 个节点**

```
init → get_pod → get_events → [classify_event 条件路由]
                                    │
                 ┌──────────┬───────┼──────────┬──────────┐
                 ↓          ↓       ↓          ↓          ↓
           query_memory  query_app  check_image  check_sched  check_config
           _metrics      _logs      _pull        _scheduling
                 └──────────┴───────┼──────────┴──────────┘
                                    ↓
                              decision (置信度估算)
                                    │
                         [route_after_decision]
                              ↙           ↘
                         rca (END)    continue_loop
                                           │
                                  [classify_event 重新路由]
```

关键设计：
- `classify_event`：根据 Event reason 字符串匹配路由到 5 条诊断路径
- `decision_node` + `route_after_decision` 分拆：LangGraph conditional_edge 函数只能返回路由字符串不能修改 state，所以决策逻辑和路由逻辑分成两个节点
- `decision_node` 置信度估算：PodStatus+KubernetesEvent=0.5, Metric=+0.3, ContainerLog=+0.2
- `rca_node`：根据证据类型匹配根因，输出 `Diagnosis` 对象
- 推理循环：`should_stop` 三重终止条件（迭代达上限 5 / 置信度 >= 0.85 / 证据充分）

**Agent Layer（diagnosis_agent.py）— 意图分类 + 工作流路由**

- `classify_intent()`：V1 关键词匹配（12 个 Pod 故障关键词 + 9 个性能关键词），V2 预留 LLM 接口
- `diagnose()`：诊断管道入口，分类意图后路由到 CrashLoopBackOff 工作流
- `_run_pod_diagnosis()`：构建初始 AgentState，调用 `graph.ainvoke()`，从最终状态提取诊断结果和证据详情

**API Layer（main.py）— FastAPI 薄封装**

| 端点 | 方法 | 功能 |
|------|------|------|
| `/diagnose` | POST | 接收自然语言问题，返回结构化诊断结果 |
| `/health` | GET | 健康检查 |

请求模型 `DiagnoseRequest`（question + user），响应模型 `DiagnoseResponse`（problem + root_cause + evidence + suggestion + confidence + reasoning_trace）。CORS 全开放。

### 2.3 测试结果

5 个 Mock 诊断场景全部通过端到端测试：

| 输入 | 路由路径 | 诊断结果 | 置信度 |
|------|----------|----------|--------|
| `nginx-oom 一直重启` | OOM → memory_metrics → rca | Memory Limit 不足导致 OOMKilled | 0.90 |
| `app-error 启动失败` | AppError → app_logs → rca | 依赖服务不可用（数据库连接失败） | 0.80 |
| `image-pull 拉不下来` | ImagePull → image_pull → rca | 镜像拉取失败 | 0.85 |
| `config-error CrashLoopBackOff` | ConfigError → config → rca | ConfigMap / Secret 配置异常 | 0.80 |
| `sched-fail 一直 Pending` | FailedScheduling → scheduling → rca | 集群资源不足导致调度失败 | 0.85 |

### 2.4 工程化配置

- `pyproject.toml`：hatchling 构建，Python >= 3.12
- 依赖：fastapi, langgraph, openai, kubernetes, mcp, pydantic-settings, uvicorn
- 开发依赖：pytest, pytest-asyncio, pytest-cov, ruff, mypy
- Ruff：target py312, line-length 100
- Mypy：strict 模式

---

## 3. 当前架构总结

### 3.1 数据流

```
POST /diagnose {"question": "nginx-oom 一直重启"}
  │
  ▼ Agent.classify_intent()
  → problem_type="pod_failure", confidence=0.90
  │
  ▼ Agent._run_pod_diagnosis()
  → graph.ainvoke(initial_state)
  │
  ▼ Workflow: init → get_pod → get_events → classify_event
  → get_pod:    K8sClient.get_pod() → EvidenceBuilder → Evidence(ev001, PodStatus)
  → get_events: K8sClient.get_events() → EvidenceBuilder → Evidence(ev002, KubernetesEvent)
  → classify:   "oomkilled" → route "oom"
  │
  ▼ query_memory_metrics → Evidence(ev003, Metric)
  │
  ▼ decision_node: 3 条 Evidence → confidence=0.95, need_more=False
  │
  ▼ route_after_decision: should_stop → "rca"
  │
  ▼ rca_node: 匹配 OOMKilled → Diagnosis(confidence=0.90)
  │
  ▼ API Response: {problem, root_cause, evidence[], suggestion, confidence, reasoning_trace[]}
```

### 3.2 关键设计决策

| 决策 | 说明 |
|------|------|
| AgentState 作为 side-car 共享上下文 | 不是流水线步骤，而是所有节点读写共享状态 |
| 7 维结构化 Evidence | 所有工具响应必须经 EvidenceBuilder 标准化后才能进入 AgentState |
| MCP 协议解耦 | 工具通过 MCP 暴露，Workflow 不直接调用底层 API |
| 推理循环三重终止 | 迭代上限 / 置信度阈值 / 证据充分性 |
| V1 关键词分类 / V2 LLM 分类 | 关键词匹配先跑通，LLM 层预留渐进替换 |
| Mock 优先开发 | 5 种故障场景模拟数据，生产环境可切换真实实现 |

### 3.3 LLM 层定位

LLM 不是独立的"第七层"，而是注入到 Agent 和 Workflow 两层内部决策节点的推理引擎：

| 注入点 | 所在层 | V1（当前） | V2（计划） |
|--------|--------|------------|------------|
| classify_intent | Agent | 12 个关键词 if/else | LLM 语义意图分类 |
| classify_event | Workflow | Event.reason 字符串匹配 | LLM 事件语义分析 |
| decision_node | Workflow | 0.5+0.3+0.2 简单累加 | LLM 多证据推理 + 下一步规划 |
| rca_node | Workflow | 关键词匹配 → 固定文案 | LLM 结合证据链生成诊断报告 |
| (远期) Tool 选择 | Workflow | 硬编码 conditional_edges | LLM Agent 自主选择 Tool（ReAct 模式） |

每个注入点可独立替换，不破坏其余架构。

---

## 4. 尚未完成的工作

### 4.1 LLM 集成（V2 核心）

- `llm/__init__.py` 当前为空，未实现共享 LLM 客户端
- 所有决策节点仍为规则驱动（关键词匹配 / 字符串匹配 / 简单置信度累加 / 模板文案）
- 计划优先级：rca_node → classify_intent → decision_node

### 4.2 真实 Kubernetes 接入

- `KubernetesClient(mode="real")` 尚未实现，抛出 `NotImplementedError`
- 需要集成 `kubernetes_asyncio` 库
- 需要处理 kubeconfig / in-cluster 两种认证方式

### 4.3 可观测性数据源集成

设计文档中定义了 7 个 Tool，目前只实现了 3 个 K8s 相关的：

| Tool | 状态 | 说明 |
|------|------|------|
| get_pod | 已实现 | K8s Pod 状态 |
| get_events | 已实现 | K8s Events |
| get_logs | 已实现 | K8s 容器日志 |
| query_logs | 未实现 | Loki 日志查询 |
| query_metrics | 未实现 | Prometheus 指标查询 |
| get_trace | 未实现 | APM Trace 查询 |
| get_alert | 未实现 | AlertManager 告警查询 |

### 4.4 工程化

- `tests/` 目录仅有 `__init__.py`，无单元测试
- 无 CI/CD 流水线
- 无 Dockerfile / 容器化
- 无 Loki / Prometheus Adapter

### 4.5 更多诊断 Workflow

- 仅实现了 CrashLoopBackOff 1 个工作流
- 设计文档中提及但未实现的场景：NodeNotReady / PVC 异常 / NetworkPolicy / Service 不可达

---

## 5. 下一步计划

### Sprint 2：LLM 接入（推荐优先）

目标：让系统从"规则引擎"升级为"AI Agent"。

```
Step 1: llm/client.py      — 共享 LLM 客户端（OpenAI SDK 封装）
Step 2: llm/prompts.py     — RCA Prompt 模板（System + Evidence 上下文注入）
Step 3: 改造 rca_node       — 规则匹配 → LLM 调用，输出结构化 Diagnosis
Step 4: 用 5 个 Mock 场景验证 LLM 输出质量
Step 5: (可选) 改造 classify_intent — 关键词 → LLM 语义分类
```

### Sprint 3：真实 K8s + 可观测性集成

- 实现 `KubernetesClient(mode="real")`
- 集成 Loki query_logs、Prometheus query_metrics
- 在真实集群中验证诊断准确性

### Sprint 4：工程化

- 单元测试覆盖（Domain / Adapter / Tool / Workflow）
- Docker 容器化
- CI/CD 流水线
- 性能测试和错误处理增强

### Sprint 5+：更多 Workflow

- NodeNotReady 诊断
- PVC 异常诊断
- NetworkPolicy 排查
- 远期：LLM Agent 自主 Tool 选择（ReAct 模式）

---

## 6. 技术栈

| 模块 | 技术 | 版本要求 |
|------|------|----------|
| Language | Python | >= 3.12 |
| API | FastAPI | >= 0.115.0 |
| Workflow | LangGraph | >= 0.2.0 |
| LLM SDK | OpenAI SDK | >= 1.50.0 |
| Kubernetes | kubernetes-python-client | >= 31.0.0 |
| MCP | MCP Python SDK | >= 1.0.0 |
| Data Models | Pydantic v2 | (via pydantic-settings >= 2.5.0) |
| ASGI Server | uvicorn | >= 0.32.0 |
| Testing | pytest + pytest-asyncio | >= 8.3.0 / >= 0.24.0 |
| Linting | ruff | >= 0.6.0 |
| Type Check | mypy (strict) | >= 1.11.0 |
| Build | hatchling | — |

---

## 7. 文件清单

### 设计文档
```
docs/
├── adr/ADR-0001.md                      — 架构决策记录
├── architecture/agent-flow.md            — Agent 整体流程设计
├── architecture/agent-state-design.md    — Agent State 6 模块设计
├── architecture/tool-design.md           — 7 个 MCP Tool 设计
└── workflow/pod-crashloopbackoff.md      — CrashLoopBackOff 诊断工作流设计
```

### 源代码
```
src/infrastructure_agent/
├── __init__.py                           — 包入口
├── main.py                               — FastAPI 应用（POST /diagnose + GET /health）
├── domain/__init__.py                    — 导出 10 个模型
├── domain/models.py                      — Pydantic 模型定义
├── adapters/__init__.py                  — 导出 KubernetesClient
├── adapters/k8s_client.py               — K8s 客户端（Mock + 5 场景）
├── mcp/__init__.py                       — 导出 server / list_tools / call_tool
├── mcp/kubernetes_server.py             — MCP Server（3 个 Tool）
├── tools/__init__.py                     — 导出 EvidenceBuilder
├── tools/evidence_builder.py             — 7 维 Evidence 转换器
├── workflow/__init__.py                  — 导出 build_crashloop_graph
├── workflow/pod_crash_workflow.py        — LangGraph 状态图（12 节点）
├── agent/__init__.py                     — 导出 classify_intent / diagnose
├── agent/diagnosis_agent.py             — 意图分类 + 工作流路由
└── llm/__init__.py                       — LLM 层（预留空文件）
```

### 配置
```
pyproject.toml                            — 项目构建 + 依赖 + 工具配置
README.md                                 — 项目说明
```
