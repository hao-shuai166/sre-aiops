# Infrastructure Agent — Project Memory

## Project Facts

- **Project**: Infrastructure Agent — AI Native SRE 平台
- **User**: ludy, 北京, K8s SRE
- **Assistant name**: Roxy, 正式风格
- **Tech stack**: Python 3.12 / FastAPI / LangGraph / MCP / Pydantic v2 / OpenAI SDK
- **Build**: hatchling, uv

## Architecture

- 6-layer: Domain → Adapter → MCP → Tool → Workflow → Agent → API
- AgentState 是 side-car 共享上下文，不是流水线步骤
- Evidence 7 维结构是进入 AgentState 的唯一证据格式
- LLM 不是独立层，注入到 Agent/Workflow 内部决策节点（V2 渐进替换）
- V1 全规则驱动，V2 逐步引入 LLM

## Current State (2026-09-03, feature/agent-investigation 分支)

- **P1 ReAct 循环已完成**：`agent/agent_workflow.py`（init → agent ⇄ execute_tool），LLM（DeepSeek）每轮自主决定调哪个工具或下结论；固定 workflow 保留，`AGENT_WORKFLOW=react|fixed` 切换（默认 react）
- 工具层 P0：`tools/registry.py`（ToolSpec/校验/memoize）+ 5 细粒度工具（get_pod_status/list_pod_events/get_container_logs/get_pod_metrics/list_pods）
- 无规则 RCA 兜底（用户决策）：LLM 挂了返回结构化错误 rca_mode=error；步数上限 8 → LLM 强收尾
- API 响应含 rca_mode（llm/error）；reasoning_trace 含每步 thought + 工具结果
- mock + 真实 DeepSeek 验证 6/6 场景通过，工具路径符合 SRE 习惯（status → events → 按需 logs/metrics）
- master（a1031d7）仍是固定 workflow 版本；5 场景真实 K8s 验证在 master 完成

## Next Steps

- P2: 新旧流程 A/B 对比（同题对比结论与调查路径质量）
- P3: 真实集群验证 ReAct 版
- 仓库卫生待用户决定：.env（含 API key）已被跟踪，建议移出并加 .gitignore（.gitignore 已写好该条目但 .env 仍被跟踪）
- 规划中方向（用户意向）：后端通用化 → MCP Client + ReAct Agent，前端提供 MCP Server 选择，K8s 逻辑拆为独立诊断增强版 MCP Server

## Key Design Decisions

- **P1 架构决策（2026-09-03）**：删除规则 RCA 兜底（LLM 挂 → 结构化错误）；细粒度 5 工具；新分支开发；超步数用 LLM 强收尾
- ReAct 决策输出必须严格 JSON（next=tool/answer 两态），非法输出带纠正提示重试 1 次
- LangGraph conditional_edge 路由函数只返回路由字符串 → decision_node + route_after_decision 分拆
- Mock 场景匹配同时支持下划线和连字符
- WorkflowState 继承 AgentState，额外属性通过 __init_node 设置
- pyproject.toml: Python >= 3.12, hatchling build

## Pitfalls Encountered

- Pydantic v2: `__fields__` 已废弃 → 用 `model_fields`
- Pod name 截断问题: `nginx-oom` 被截断为 `nginx` → 改用完整匹配
- LangGraph conditional_edge 签名: 只能返回路由字符串
- State 额外属性 `_pod`/`_namespace` 不被 Pydantic 识别, LangGraph 节点间传递丢失 → 改用 `wf_pod`/`wf_namespace`/`wf_cluster`
- Mock `image_pull` 缺 `ImagePullBackOff` event reason → classify_event 路由错误
- `sched-fail` pod 名不匹配 `failed_scheduling` scenario key → _pick_scenario alias 映射缺失
