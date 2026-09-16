# Infrastructure Agent

AI Native Infrastructure Operations Platform — 面向企业级 SRE 场景的 AI 运维智能体平台。

**当前版本 v0.2.0**：Kubernetes Pod 故障诊断已端到端可用 — LLM ReAct Agent 自主调查、证据链可验证、Web 控制台单镜像交付。

## 1. 项目定位

Infrastructure Agent 不是一个简单的聊天机器人。它的核心目标是将 SRE 故障排查经验，通过 **LLM + Workflow + Tool + Observability 数据** 进行结构化沉淀，使 AI 能够完成基础设施诊断、根因分析（RCA）和辅助运维。

> 用户输入一个 Kubernetes Pod 异常问题 → LLM Agent 自主选择工具、逐层取证 → 输出带证据链的 RCA。

```
用户: 为什么 nginx-oom Pod 一直 CrashLoopBackOff？

Agent（ReAct 循环，LLM 每轮自主决策）:
  1. get_pod_status     → CrashLoopBackOff, exit 137, 15 次重启     (ev001)
  2. list_pod_events    → OOMKilled: exceeded memory limit 512Mi    (ev002)
  3. get_pod_metrics    → 内存 usage 512Mi / limit 512Mi (100%)     (ev003)
  4. 输出结论: OOMKilled，内存 Limit 不足，建议调高 Limit
     └── 结论必须引用真实证据 ID（ev001/ev002/ev003），无有效证据不予接受
```

## 2. 核心设计理念

### Evidence Driven（证据驱动信任）

所有结论必须基于真实数据（Kubernetes API、Metrics、Logs、Events），禁止 LLM 凭空猜测。结论的 `evidence` 字段必须显式引用本次调查的证据 ID；引用不存在的证据、或只引用错误类证据的结论会被校验层拒绝并要求重写。

### ReAct Agent（LLM 自主调查）

不预设固定排障路径。LLM 每轮根据已收集的证据自主决定：调哪个工具、传什么参数，还是已经足够下结论。工具粒度按 SRE 习惯拆分（status → events → 按需 logs/metrics），步数上限 8 步熔断强收尾。

### Tool First

所有基础设施能力必须通过 Tool 层暴露，Agent 不直接调用底层 API。Tool 注册表负责参数校验、请求级缓存（同一调查内相同调用不重复执行）与审计元数据记录。

## 3. 架构概览

```
Vue 3 控制台 ──┐
               ▼
          FastAPI (/diagnose)
               ▼
          Agent 层（ReAct 循环 · LangGraph）
          │  init → agent ⇄ execute_tool → END
          │  · LLM 决策（严格 JSON，非法输出纠正重试）
          │  · 目标解析 target_resolver（pod/ns/cluster 提取）
          ▼
          Tool 层（ToolRegistry · ToolSpec 校验 · 请求级 ToolCache）
               ▼
          Adapter 层（KubernetesClient: mock / real 双模式）
               ▼
          Kubernetes API（真实集群 / 5 个内置 mock 场景）
```

**请求级隔离**：LangGraph 图是进程级单例，但每次调查的缓存与证据构建器通过 `contextvars` 注入请求级 `InvestigationRuntime`——并发诊断互不干扰，证据 ID 各自从 ev001 起编号。

## 4. 技术栈

| 模块            | 技术                                       |
| --------------- | ------------------------------------------ |
| Language        | Python 3.12+                               |
| API             | FastAPI + Uvicorn                          |
| Workflow        | LangGraph（ReAct 状态图）                  |
| LLM             | OpenAI SDK 兼容接口（DeepSeek 等）         |
| Kubernetes      | kubernetes-python-client                   |
| Frontend        | Vue 3 + Vite + Element Plus                |
| Delivery        | Docker 多阶段构建（前后端单镜像）          |
| Configuration   | Pydantic v2 / 环境变量                     |
| Testing         | pytest + pytest-asyncio                    |
| Package Manager | uv / hatchling                             |

## 5. 项目结构

```
infrastructure-agent/
├── src/infrastructure_agent/
│   ├── main.py                    # FastAPI 入口 + 前端托管
│   ├── agent/
│   │   ├── agent_workflow.py      # ReAct 图（init → agent ⇄ execute_tool）
│   │   ├── diagnosis_agent.py     # 意图分类、图选择、结果汇总
│   │   └── target_resolver.py     # 用户输入 → TargetContext（pod/ns/cluster）
│   ├── workflow/
│   │   └── pod_crash_workflow.py  # V1 固定流程（保留作 A/B 与回滚）
│   ├── tools/
│   │   ├── registry.py            # ToolSpec 定义、参数校验、请求级缓存
│   │   ├── k8s_tools.py           # 5 个细粒度 K8s 调查工具
│   │   └── evidence_builder.py    # 原始响应 → 7 维 Evidence
│   ├── llm/
│   │   ├── client.py              # OpenAI 兼容客户端封装
│   │   ├── agent_prompts.py       # ReAct 决策 / 强制收尾 prompt
│   │   └── prompts.py             # 固定流程 RCA prompt
│   ├── domain/
│   │   └── models.py              # AgentState / Evidence / Diagnosis 等领域模型
│   ├── adapters/
│   │   └── k8s_client.py          # K8s 客户端（mock 5 场景 / real 三级探测）
│   └── mcp/
│       └── kubernetes_server.py   # MCP Server 形态的工具暴露（演进中）
├── frontend/                      # Vue 3 诊断控制台（构建产物由后端托管）
├── tests/                         # 单元测试（决策校验 / 目标解析 / 工具层）
├── k8s/                           # 集群部署清单
├── manifests/                     # 测试故障场景清单（OOM / 配置错误 / 调度失败）
├── docs/                          # 架构文档 / ADR / Workflow 设计
└── Dockerfile                     # 三阶段构建：node → python → 运行时
```

## 6. 快速开始

### 环境要求

- Python >= 3.12，uv（推荐）
- 一个 OpenAI 兼容 LLM API Key（如 DeepSeek）

### 本地运行

```bash
git clone <repo-url>
cd infrastructure-agent
uv sync

# 配置环境变量（也可写入 .env，已被 gitignore）
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://api.deepseek.com/v1   # 可选
export LLM_MODEL=deepseek-chat                        # 可选
export K8S_MODE=mock                                  # mock=内置场景，real=真实集群

uv run uvicorn infrastructure_agent.main:app --reload
```

- API 文档：http://localhost:8000/docs
- 诊断控制台（需先构建前端）：http://localhost:8000/

### 配置项

| 环境变量          | 默认值                     | 说明                                   |
| ----------------- | -------------------------- | -------------------------------------- |
| `OPENAI_API_KEY`  | （必填）                   | LLM API Key                            |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容接口地址                    |
| `LLM_MODEL`       | `gpt-4o-mini`              | 模型名                                 |
| `K8S_MODE`        | `mock`                     | `mock` 内置 5 场景 / `real` 真实集群   |
| `K8S_CLUSTER`     | `prod`                     | 集群标签（用于响应标识）               |
| `AGENT_WORKFLOW`  | `react`                    | `react` LLM 自主 / `fixed` 固定流程    |

### 调用诊断 API

```bash
curl -X POST http://localhost:8000/diagnose \
  -H "Content-Type: application/json" \
  -d '{"question": "为什么 nginx-oom Pod 一直 CrashLoopBackOff？"}'
```

响应（节选）：

```json
{
  "problem": "Pod OOMKilled 导致反复 CrashLoopBackOff",
  "root_cause": "容器 app 退出码 137（OOMKilled），内存用量达到 Limit 512Mi（ev002, ev003）",
  "evidence": [
    {"id": "ev001", "type": "PodStatus", "content": {"status": "CrashLoopBackOff", "exit_code": 137}, "confidence": 0.95},
    {"id": "ev002", "type": "KubernetesEvent", "content": {"events": [{"reason": "OOMKilled", "message": "Container exceeded memory limit of 512Mi"}]}, "confidence": 0.95}
  ],
  "suggestion": "调高容器内存 Limit（当前 512Mi）或排查应用内存泄漏",
  "confidence": 0.9,
  "rca_mode": "llm",
  "reasoning_trace": [{"step": 1, "observation": "Agent decision: call get_pod_status({...})", "conclusion": "先看 Pod 状态"}]
}
```

`rca_mode` 说明结论产出方式：`llm`（LLM 自主调查完成）/ `error`（LLM 不可用或决策无效，结构化报错——按设计无规则兜底）。

### Docker 部署（前后端单镜像）

```bash
docker build -t infrastructure-agent:0.2.0 .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-xxx \
  -e OPENAI_BASE_URL=https://api.deepseek.com/v1 \
  -e K8S_MODE=real \
  -v ~/.kube:/root/.kube:ro \
  infrastructure-agent:0.2.0
```

镜像内已包含构建好的前端，浏览器直接访问 http://<host>:8000/ 即为诊断控制台。

### 内置 Mock 场景（开发/演示）

| Pod 名（示例）      | 场景                | 典型路径                          |
| ------------------- | ------------------- | --------------------------------- |
| `nginx-oom`         | OOMKilled           | status → events → metrics         |
| `nginx-app-error`   | 应用启动失败        | status → events → logs            |
| `nginx-image-pull`  | 镜像拉取失败        | status → events                   |
| `nginx-config-error`| 配置/挂载错误       | status → events → logs            |
| `sched-failpod`     | 调度失败            | status → events                   |

### 运行测试

```bash
uv run pytest        # 36 个单元测试：决策校验 / 目标解析 / 工具层与审计元数据
```

## 7. 关键机制说明

- **证据引用校验**：LLM 结论必须携带非空 `evidence` 数组；引用的 ID 必须存在于本次调查，且至少包含一条非错误证据（错误证据仅可作排除性依据）。
- **请求级 InvestigationRuntime**：工具缓存与证据计数器按请求隔离（`contextvars`），并发诊断零交叉污染。
- **内部查询审计**：`get_container_logs` 省略容器名时自动取首容器，这一隐式 `get_pod` 查询会记录在 `_meta.internal_queries`，并透出到最终证据中——审计链完整。
- **步数熔断**：工具调用达 8 步上限后切换强制收尾 prompt，要求 LLM 基于已有证据给出最优结论。

## 8. 路线图

| 阶段     | 状态 | 内容                                                             |
| -------- | ---- | ---------------------------------------------------------------- |
| V1 MVP   | ✅   | 固定 Workflow 的 Pod 诊断（LangGraph 状态图）                    |
| V2 P1    | ✅   | ReAct Agent：LLM 自主选工具、证据引用校验、请求级隔离、Web 控制台 |
| V2 P2    | ⏳   | 新旧流程 A/B 对比（同题对比结论与调查路径质量）                  |
| V2 P3    | ⏳   | 真实集群验证 ReAct 版                                            |
| V3 方向  | 📐   | MCP 通用化：后端演进为 MCP Client + ReAct Agent，前端提供 MCP Server 选择，K8s 诊断能力拆为独立诊断增强版 MCP Server |

## 9. 开发原则

新增功能前必须回答以下问题：

1. 它属于哪个 Domain？
2. 它属于哪个 Workflow？
3. 它需要哪些 Tool？
4. 数据来源是什么？
5. 如何验证结果正确？

## License

MIT
