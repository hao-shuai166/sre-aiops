# Infrastructure Agent

AI Native Infrastructure Operations Platform — 面向企业级 SRE 场景的 AI 运维智能体平台。

## 1. 项目定位

Infrastructure Agent 不是一个简单的聊天机器人。它的核心目标是将 SRE 故障排查经验，通过 **LLM + Workflow + MCP Tool + Observability 数据** 进行结构化沉淀，使 AI 能够完成基础设施诊断、根因分析（RCA）和辅助运维。

### V1 目标

实现 Kubernetes 场景下的智能故障诊断。第一个 MVP 场景：

> 用户输入一个 Kubernetes Pod 异常问题 → Agent 自动完成分析并输出 RCA。

```
用户: 为什么 nginx-xxx Pod 一直 CrashLoopBackOff？

Agent 自动执行:
  1. 获取 Pod 状态
  2. 获取 Container 状态
  3. 查询 Kubernetes Events
  4. 获取 Pod Logs
  5. 分析异常原因
  6. 输出故障原因和建议方案
```

## 2. 核心设计理念

### Evidence Driven

所有结论必须基于真实数据（Kubernetes API、Metrics、Logs、Trace、Events），禁止 LLM 凭空猜测。

### Workflow is Knowledge

Workflow 代表 SRE 的排障经验，是项目最核心的资产。每个故障场景对应一个可复用的诊断 Workflow。

### Tool First

所有基础设施能力必须通过 Tool 层暴露，Workflow 不直接调用底层 API，而是通过 Tool 抽象访问。

## 3. 架构概览

```
User → LLM → Workflow Engine → MCP / Tool Layer → Infrastructure Adapter → Kubernetes / Prometheus / Loki / DeepFlow / APM
```

## 4. 技术栈

| 模块            | 技术                       |
| --------------- | -------------------------- |
| Language        | Python 3.12+               |
| API             | FastAPI                    |
| Workflow        | LangGraph                  |
| LLM SDK         | OpenAI SDK                 |
| Kubernetes      | kubernetes-python-client   |
| MCP             | MCP Python SDK             |
| Configuration   | Pydantic Settings          |
| Testing         | pytest                     |
| Package Manager | uv                         |

## 5. 项目结构

```
infrastructure-agent/
├── docs/                          # 文档
│   ├── architecture/              # 架构设计文档
│   ├── adr/                       # 架构决策记录 (ADR)
│   ├── workflow/                  # Workflow 设计文档
│   └── roadmap/                   # 路线图
├── src/
│   └── infrastructure_agent/      # 主代码包
│       ├── agent/                 # Agent 层：意图理解、Workflow 选择、结果汇总
│       ├── workflow/              # Workflow 层：排障流程定义
│       ├── tools/                 # Tool 层：基础能力封装
│       ├── mcp/                   # MCP Server 实现
│       ├── llm/                   # LLM 交互封装
│       ├── domain/                # 领域对象定义
│       └── adapters/              # 基础设施适配器
├── tests/                         # 测试
├── examples/                      # 使用示例
├── pyproject.toml                 # 项目配置
└── README.md                      # 项目说明
```

## 6. 快速开始

### 环境要求

- Python >= 3.12
- uv（推荐包管理器）

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd infrastructure-agent

# 创建虚拟环境并安装依赖
uv sync
```

### 验证安装

```bash
uv run pytest
```

## 7. 开发计划

| Sprint   | 目标                       |
| -------- | -------------------------- |
| Sprint 0 | Foundation：项目基础搭建    |
| Sprint 1 | Pod Diagnosis：首个 Workflow |
| Sprint 2 | Kubernetes Integration     |
| Sprint 3 | Agent MVP：端到端流程      |

## 8. 开发原则

新增功能前必须回答以下问题：

1. 它属于哪个 Domain？
2. 它属于哪个 Workflow？
3. 它需要哪些 Tool？
4. 数据来源是什么？
5. 如何验证结果正确？

## License

MIT
