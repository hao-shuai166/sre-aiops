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

## Current State (2026-09-16, master)

- **P1 ReAct 循环已完成并合入 master**：`agent/agent_workflow.py`（init → agent ⇄ execute_tool），LLM（DeepSeek）每轮自主决定调哪个工具或下结论；固定 workflow 保留，`AGENT_WORKFLOW=react|fixed` 切换（默认 react）
- **Vue 3 + Element Plus 前端已上线**（单镜像，FastAPI StaticFiles 托管 frontend/dist）
- **外部评审最小实现包 4 项全部完成**（2026-09-16）：① Evidence 显式引用+校验（2841623）② 请求级 InvestigationRuntime via contextvars（62cd0a5）③ target_resolver + TargetContext，cluster 走 K8S_CLUSTER env（b7bf166）④ tail integer schema + get_container_logs 内部查询审计元数据（_meta.container_source/internal_queries/tail，透出到 Evidence content）
- 工具层：`tools/registry.py`（ToolSpec/校验/请求级 ToolCache）+ 5 细粒度工具；36 单测全过
- 无规则 RCA 兜底（用户决策）：LLM 挂了返回结构化错误 rca_mode=error；步数上限 8 → LLM 强收尾

## Next Steps

- P2: 新旧流程 A/B 对比（同题对比结论与调查路径质量）
- P3: 真实集群验证 ReAct 版
- **下一大项：MCP Client 化讨论**（后端通用化 → MCP Client + ReAct Agent，前端提供 MCP Server 选择，K8s 逻辑拆为独立诊断增强版 MCP Server）
- 仓库卫生：frontend/dist 已被提交（建议日后 gitignore）

## Key Design Decisions

- **P1 架构决策（2026-09-03）**：删除规则 RCA 兜底（LLM 挂 → 结构化错误）；细粒度 5 工具；超步数用 LLM 强收尾
- **评审裁决（2026-09-16）**：不做八组件 Runtime、facts/signals 语义层、ReasoningControl 变 LLM 终止器；保留步数熔断+强收尾；get_container_logs 自动解析容器保留但补审计元数据
- ReAct 决策输出必须严格 JSON（next=tool/answer 两态），非法输出带纠正提示重试 1 次
- answer 必须携带非空 evidence 引用数组，ID 须存在且引用集至少含一条非错误证据（错误证据仅可作排除性依据）
- LangGraph 图是进程级单例，请求级状态（缓存/EvidenceBuilder）经 contextvars.ContextVar 传播
- LangGraph conditional_edge 路由函数只返回路由字符串 → decision_node + route_after_decision 分拆
- Mock 场景匹配同时支持下划线和连字符
- pyproject.toml: Python >= 3.12, hatchling build

## Pitfalls Encountered

- Pydantic v2: `__fields__` 已废弃 → 用 `model_fields`
- Pod name 截断问题: `nginx-oom` 被截断为 `nginx` → 改用完整匹配
- LangGraph conditional_edge 签名: 只能返回路由字符串
- State 额外属性 `_pod`/`_namespace` 不被 Pydantic 识别, LangGraph 节点间传递丢失 → 改用 `wf_pod`/`wf_namespace`/`wf_cluster`
- Mock `image_pull` 缺 `ImagePullBackOff` event reason → classify_event 路由错误
- `sched-fail` pod 名不匹配 `failed_scheduling` scenario key → _pick_scenario alias 映射缺失
- 本机 Clash 代理会拦 Python openai SDK（curl 正常）：跑 LLM 相关脚本前 unset http_proxy/https_proxy
- Windows Git Bash 环境下 sed/head/grep/rm 等基础命令常不可用 → 用 Edit 工具 / PowerShell 替代
