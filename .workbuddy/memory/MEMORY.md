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

## Current State (2026-07-31)

- Sprint 1 完成：6 层代码 + 5 个 Mock 场景端到端测试通过
- **5/5 场景真实 K8s 集群验证全部通过**（CrashLoopBackOff / ImagePullBackOff / OOMKilled / ConfigError / FailedScheduling）
- 设计文档 4 份 + ADR-0001 全部评审通过
- **LLM 层已接入 rca_node 并验证生效**：DeepSeek v4 Pro (api.deepseek.com) + RCA Prompts + 规则 fallback
- PodStatus 支持 lastState 捕获（last_exit_code / last_reason / last_message）
- Events 查询按 Pod name + kind=Pod 过滤
- classify_event 两级检查（PodStatus 容器 reason + Events reason/message）
- decision_node 按诊断路径分流 confidence（避免死循环）
- API 层全局异常捕获：工作流异常返回结构化错误而非裸 500
- classify_intent 和 decision_node 仍为规则驱动（下一步 LLM 化候选）

## Key Design Decisions

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
