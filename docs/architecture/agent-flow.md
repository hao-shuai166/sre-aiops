# Infrastructure Agent 请求处理流程设计

> Version: v2.0  
> Status: Design Draft

---

# 1. 概述

## 1.1 背景

随着 Kubernetes、微服务、云原生架构的发展，基础设施系统越来越复杂。

传统运维方式通常依赖：

- 人工经验
- 固定 SOP
- 运维脚本


典型流程：

```
发现问题

↓

人工判断

↓

执行命令

↓

分析结果

↓

解决问题
```

这种模式存在：

- 问题类型不可预测
- 排查过程依赖个人经验
- 多系统数据无法关联
- 故障定位效率低


Infrastructure Agent 的目标：

> 构建一个面向基础设施领域的 AI Agent，使其能够理解用户问题、自主规划诊断路径、调用基础设施工具获取证据，并基于证据生成可追溯的故障分析结果。


---

# 2. 核心设计原则


Infrastructure Agent 遵循以下原则：

## 2.1 Agent 负责决策

Agent 负责：

- 理解用户意图
- 判断问题类型
- 选择 Workflow
- 制定诊断计划


---

## 2.2 Workflow 负责领域流程


Workflow 负责：

- 定义某类问题的排查方法
- 控制诊断步骤
- 管理领域规则


例如：

```
Pod CrashLoopBackOff Workflow

Node Failure Workflow

Network Diagnosis Workflow
```


---

## 2.3 Tool 负责能力调用


Tool 负责：

- 查询 Kubernetes
- 查询监控
- 查询日志
- 查询 Trace


Agent 不直接操作基础设施。


---

# 3. 整体架构


```
                         User

                          |

                          v


                  +---------------+

                  | Agent Layer   |

                  |               |

                  | Intent        |

                  | Planning      |

                  | Routing       |

                  +---------------+

                          |

                          v


                  +---------------+

                  | Workflow      |

                  | Engine        |

                  +---------------+

                          |

          +---------------+---------------+

          |                               |

          v                               v


       MCP Tool                    Reasoning Loop


          |                               |

          v                               |


 Kubernetes / Prometheus                  |

 / Loki / APM                             |

                                          |

    +-------------------------------------+

    |

    v

+-------------------------------------------------+

|               Agent State (共享上下文)              |

|  Request | Intent | Execution | Evidence | RCA   |

+-------------------------------------------------+

    ^          ^          ^          ^

    |          |          |          |

    +----------+----------+----------+

    (所有组件通过 LangGraph State 统一读写)

```

---

# 4. Agent Layer


Agent Layer 是系统入口。


主要职责：

- 理解问题
- 判断方向
- 选择能力


---

# 4.1 Intent Analysis


功能：

理解用户输入。


例如：

用户：

```
nginx Pod一直重启
```


分析：

```yaml
problem_domain:

  kubernetes


problem_type:

  pod_failure


confidence:

  0.95
```

---

# 4.2 Problem Router


负责：

根据问题类型选择 Workflow。


例如：

```
Pod异常

      |

      v

Pod Diagnosis Workflow
```


---

# 4.3 Unknown Problem


现实中很多问题无法直接分类。


例如：

```
系统最近不太稳定，帮忙看看
```


此时进入：

```
Investigation Workflow
```


而不是让 LLM 无限自由探索。


---

# 5. Investigation Workflow


## 5.1 设计目标


Investigation Workflow 用于：

> 收集系统状态，使未知问题逐渐明确。


---

## 5.2 工作流程


```
用户问题

    |

    v

基础信息收集

    |

    v

发现异常方向

    |

    v

进入具体 Workflow

```


---

## 5.3 示例


用户：

```
线上系统异常
```


执行：

```
查询 Alert

↓

查询异常服务

↓

查询错误率

↓

定位 Namespace

↓

定位 Pod

↓

进入对应诊断流程
```


---

# 6. Workflow Layer


Workflow 是具体问题解决流程。


特点：

- 输入明确
- 步骤明确
- 输出明确


---

# 6.1 Kubernetes Workflow


## Pod Diagnosis Workflow


处理：

- CrashLoopBackOff
- OOMKilled
- Container启动失败
- Image异常


---

## Node Diagnosis Workflow


处理：

- Node NotReady
- DiskPressure
- MemoryPressure


---

## Network Diagnosis Workflow


处理：

- Service访问失败
- DNS异常
- NetworkPolicy异常


---

# 7. Workflow Router


Workflow Router 不完全依赖 LLM。


采用：

```
LLM分类

+

规则判断
```


---

示例：


用户：

```
Pod一直重启
```


第一层：

```
Kubernetes Pod问题
```


第二层：

进入：

```
Pod Diagnosis Workflow
```


Workflow内部继续判断：


```
Evidence

    |

    +---- OOMKilled

    |

    +---- CrashLoopBackOff

    |

    +---- Image Error

```


---

# 8. Agent State


## 8.1 为什么需要 State


故障分析不是一次查询。


真实流程：

```
查询

↓

分析

↓

发现缺少信息

↓

继续查询

↓

重新分析
```


因此需要保存中间状态。


---

## 8.2 State 内容


Agent State 的完整设计见 **[agent-state-design.md](agent-state-design.md)**。

核心数据模型包含六个顶层模块：

| 模块 | 说明 |
|------|------|
| `request` | 用户原始请求上下文 |
| `intent` | Agent 对问题的理解和分类 |
| `execution` | 当前 Workflow 执行状态（含循环控制） |
| `evidence` | 结构化证据集合（含 id / source / timestamp / confidence） |
| `reasoning` | 推理历史记录 |
| `diagnosis` | 最终诊断结果（通过 Evidence ID 反向引用） |

简化示意：

```yaml
AgentState:

  request:

  intent:

  execution:

  evidence:

  reasoning:

  diagnosis:

```

---

# 9. Evidence Driven Diagnosis


所有结论必须基于 Evidence。


例如：

错误：

```
可能是内存不足
```


正确：

```
根因:

Pod OOM


证据:

1. Container Exit Code = 137

2. Event:
   OOMKilled

3. Memory Usage:
   512Mi / 512Mi

```


---

# 10. Reasoning Loop


## 10.1 为什么需要循环


真实排障不是线性的。


例如：

第一次：

查询 Pod：

发现：

```
restartCount增加
```


继续查询：

Events:

发现：

```
OOMKilled
```


继续查询：

Metrics:

确认：

```
Memory持续超过limit
```


---

## 10.2 Loop流程


```
获取Evidence

      |

      v

分析Evidence

      |

      v

信息是否足够？

      |

 +----+----+

 |         |

是         否

 |          |

 v          v

RCA      继续调用Tool

            |

            |

            +----重新分析

```


---

# 11. MCP Tool Layer


MCP 是 Tool 的标准化协议。


作用：

让 Agent 能够统一调用外部能力。


架构：


```
Workflow

    |

    v

MCP Client

    |

    v

MCP Server

    |

    v

Infrastructure API

```


---

# 12. Tool 示例


## Kubernetes Tool


```
get_pod()

get_events()

get_logs()

get_node()

get_deployment()

```


---

## Monitoring Tool


```
query_metric()

query_alert()

```


---

## Logging Tool


```
query_logs()

```


---

## APM Tool


```
query_trace()

get_service_map()

```


---

# 13. 异常处理


所有 Tool 必须返回标准结果。


成功：


```json
{
 "status":"success",
 "data":{}
}
```


失败：


```json
{
 "status":"error",
 "error_type":"Timeout",
 "message":"Kubernetes API timeout"
}
```


---

Agent 根据异常决定：

- 重试
- 更换数据源
- 降级处理
- 告知用户


---

# 14. LangGraph 在系统中的位置


LangGraph 是 Agent Workflow 的实现框架。


它负责：

- 节点管理
- 状态流转
- 条件判断
- 循环执行


例如：


```
START

 |

Intent Node

 |

Router Node

 |

Pod Workflow

 |

Tool Node

 |

Evidence Node

 |

Decision Node

 |

RCA Node

 |

END

```


---

# 15. 完整请求流程


```
                     用户请求

                         |

                         v


                 Intent Analysis


                         |

                         v


                 Problem Router


                         |

          +--------------+--------------+

          |                             |

          v                             v


     Known Problem              Unknown Problem


          |                             |

          v                             v


    Specific Workflow        Investigation Workflow


          |                             |

          +--------------+--------------+

                         |

                         v


                  Workflow Engine


                         |

                         v


                    Agent State


                         |

                         v


                  MCP Tool调用


                         |

                         v


                  Evidence收集


                         |

                         v


                 Reasoning Loop


                         |

                         v


                       RCA


```

---

# 16. 当前开发路线


阶段一：

```
完成 Agent Flow设计

↓

完成第一个 Workflow

↓

设计 MCP Tool

↓

实现 LangGraph

↓

接入 Kubernetes
```


第一阶段目标：

实现：

```
Kubernetes Pod Diagnosis Agent
```


---

# 17. 后续演进方向


## 自动修复

支持：

- Restart Pod
- Scale Deployment
- 修改配置
- Rollback


---

## 知识库


接入：

- SOP
- 历史故障
- 工单


---

## Multi-Agent


未来拆分：


```
Kubernetes Agent

Monitoring Agent

Network Agent

Database Agent

Security Agent
```

形成：

Infrastructure Multi-Agent System

```
```