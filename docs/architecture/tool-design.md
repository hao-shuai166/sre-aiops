# Infrastructure Agent Tool Design

> Version: v1.0  
> Status: Design Draft


---

# 1. 概述

Tool Layer 是 Infrastructure Agent 与真实基础设施之间的连接层。

Agent 不直接访问：

- Kubernetes API
- Prometheus
- Loki
- APM
- DeepFlow

而是通过 Tool 获取结构化信息。

整体关系：

```
Agent

 |

 v

Workflow

 |

 v

Tool

 |

 v

Infrastructure API

 |

 v

Evidence
```

Tool Layer 是 Agent 获取外部世界信息的唯一入口。

---

# 2. Tool Layer 设计目标

## 2.1 标准化能力

不同基础设施系统：

```
Kubernetes

Prometheus

Loki

DeepFlow

Database
```

提供统一调用方式。


---

## 2.2 Evidence 输出

Tool 返回结果不能只是：

```
字符串
```

或者：

```
原始 API Response
```

必须转换为：

```
Evidence
```

供 Agent 推理。


---

## 2.3 安全控制

Tool 负责：

- 参数校验
- 权限控制
- 调用限制
- 操作审计


例如：

查询：

```
get_pod()
```

默认允许。


修改：

```
delete_pod()
restart_pod()
```

需要额外权限。


---

# 3. Tool Layer 整体架构


```
                  Agent


                    |

                    v


                Workflow


                    |

                    v


              Tool Interface


                    |

          +---------+---------+

          |         |         |

          v         v         v


     Kubernetes Prometheus Loki


          |         |         |

          +---------+---------+

                    |

                    v


        Evidence Builder  ← Tool Layer 统一组件
              |            （各 MCP Server 共享，不各自实现）

              v


          Agent State


```

> **Evidence Builder 定位**: 它是 Tool Layer 的统一组件，不属于任何单个 MCP Server。所有 MCP Server 返回 Raw Response 后，由 Evidence Builder 统一转换为 Evidence 再写入 Agent State。这样各 Server 只关心数据获取，Evidence 转换规则集中管理。

---

# 4. MCP 定位


MCP（Model Context Protocol）作为 Tool 标准协议。


它解决：

> 如何让 AI 标准化调用外部能力。


架构：


```
                Agent


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

# 5. MCP Server 拆分策略


第一阶段：

按照基础设施系统拆分。


目录：

```
mcp-server

├── kubernetes

├── prometheus

├── loki

└── deepflow

```


原因：

- 边界清晰
- 易维护
- 易扩展
- 符合基础设施领域划分


## 5.2 Tool 发现机制


Workflow 不硬编码 Tool 名称列表。

Agent 通过 MCP 协议的 `list_tools()` 方法动态发现可用 Tool：

```
Agent
  |
  v
MCP Client
  |
  v
list_tools()
  |
  v
[get_pod, get_events, get_logs, query_metric, ...]
```

每个 Tool 携带元信息：

| 属性 | 说明 |
|------|------|
| name | Tool 唯一标识（如 `get_pod`） |
| description | Tool 功能描述，供 LLM/Workflow 理解用途 |
| inputSchema | JSON Schema 定义参数格式 |

实现路径：

- LangGraph `ToolNode` 直接绑定 MCP Tool Manager
- Workflow 通过 `tool_name` 引用 Tool，而非硬编码类实例
- `list_tools()` 返回的 schema 即 Tool 的调用契约

优点：

- 新增 Tool 无需修改 Workflow 代码
- Tool 可独立开发、测试、部署
- 支持运行时启用/禁用特定 Tool


---

# 6. Tool 接口设计原则


所有 Tool 必须定义：

```
Input Schema

Output Schema

Error Schema
```


其中：

Input：

定义调用参数。


Output：

定义返回结构。


Error：

定义异常类型。


---

# 7. Kubernetes Tool 设计


## 7.1 get_pod


### 功能

获取 Kubernetes Pod 当前状态。


### Input

```json
{
  "cluster": "prod",
  "namespace": "default",
  "pod": "nginx-xxx"
}
```


### Output

```json
{
  "status": "Running",
  "restart_count": 3,
  "containers": [
    {
      "name": "nginx",
      "state": "Running"
    }
  ]
}
```


### Evidence

```yaml
id: ev001

type: PodStatus

source:
  system: kubernetes
  api: pods

timestamp: "2026-07-23T10:00:00Z"

resource:
  namespace: default
  pod: nginx-xxx

content:
  status: Running
  restart_count: 3

confidence: 0.95
```


---

## 7.2 get_events


### 功能

获取 Kubernetes Event。


### Input


```json
{
  "cluster": "prod",
  "namespace": "default",
  "resource": "pod/nginx"
}
```


### Output


```json
{
  "events": [
    {
      "reason": "OOMKilled",
      "message":
      "Container exceeded memory limit"
    }
  ]
}
```


### Evidence


```yaml
id: ev002

type: KubernetesEvent

source:
  system: kubernetes
  api: events

timestamp: "2026-07-23T10:00:01Z"

resource:
  namespace: default
  pod: nginx-xxx

content:
  reason: OOMKilled
  message: Container exceeded memory limit

confidence: 0.95
```


---

## 7.3 get_logs


### 功能

获取 Pod 容器的 stdout/stderr 日志（容器级实时日志）。

> **使用场景**: 查看单个 Pod 的容器输出，粒度到容器级别。集群级关键词搜索请用 §9 Loki `query_logs`。


### Input


```json
{
  "cluster": "prod",
  "namespace": "default",
  "pod": "nginx",
  "container": "nginx",
  "tail": 200
}
```


### Output


```json
{
  "logs":
  [
    "connection timeout"
  ]
}
```


### Evidence


```yaml
id: ev003

type: ContainerLog

source:
  system: kubernetes
  api: logs

timestamp: "2026-07-23T10:00:02Z"

resource:
  namespace: default
  pod: nginx-xxx
  container: nginx

content:
  logs:
    - "connection timeout"

confidence: 0.90
```


---

# 8. Monitoring Tool


## 8.1 query_metric


### 功能

查询监控指标。


### Input


```json
{
  "cluster": "prod",
  "namespace": "default",
  "query":

  "container_memory_usage_bytes",

  "time_range":

  "1h"
}
```


### Output


```json
{
  "series": []
}
```


### Evidence


```yaml
id: ev004

type: Metric

source:
  system: prometheus
  api: query_range

timestamp: "2026-07-23T10:00:00Z"

resource:
  namespace: default
  pod: nginx-xxx

content:
  query: container_memory_usage_bytes
  series: [...]

confidence: 0.95
```


---

## 8.2 query_alert


### 功能

查询告警。


### Input


```json
{
  "severity": "critical"
}
```


### Output


```json
{
  "alerts": []
}
```


### Evidence


```yaml
id: ev005

type: Alert

source:
  system: prometheus
  api: alerts

timestamp: "2026-07-23T10:00:00Z"

resource:
  namespace: default

content:
  severity: critical
  alerts: [...]

confidence: 0.90
```


---

# 9. Loki Tool


## query_logs


### 功能

查询集群级中心化日志（基于关键词搜索）。

> **使用场景**: 跨 Pod / 跨 Service 的关键词日志搜索。容器级实时日志请用 §7.3 K8s `get_logs`。


### Input


```json
{
  "namespace":

  "production",

  "keyword":

  "error",

  "time_range":

  "30m"
}
```


### Output


```json
{
  "logs": []
}
```


### Evidence


```yaml
id: ev006

type: CentralizedLog

source:
  system: loki
  api: query_range

timestamp: "2026-07-23T10:00:00Z"

resource:
  namespace: production

content:
  keyword: error
  logs: [...]

confidence: 0.85
```


---

# 10. DeepFlow / APM Tool


## query_trace


### 功能

查询调用链。


### Input


```json
{
  "service":

  "order-service",

  "time_range":

  "10m"
}
```


### Output


```json
{
  "trace":

  [
    {
      "service":

      "mysql",

      "latency":

      "5s"
    }
  ]
}
```


### Evidence


```yaml
id: ev007

type: Trace

source:
  system: deepflow
  api: traces

timestamp: "2026-07-23T10:00:00Z"

resource:
  service: order-service

content:
  services:
    - mysql
  max_latency: 5s

confidence: 0.85
```


---

# 11. Tool 错误模型


所有 Tool 返回统一格式。


## 成功


```json
{
  "result":

  "success",

  "data": {}
}
```


---

## 失败


```json
{
  "result":

  "error",

  "error_type":

  "Timeout",

  "message":

  "Prometheus query timeout"
}
```


---

# 12. Error 处理策略


Agent 根据错误类型决定。


## Retry


适用于：

```
Timeout

Network Error
```


动作：

重新执行。


---

## Skip


适用于：

```
数据不存在
```


动作：

继续其他证据收集。


---

## Stop


适用于：

```
Kubernetes API 不可用
```


动作：

终止诊断。


---

# 13. Tool 权限模型


Tool 分级。


---

## Read Tool


默认允许。


例如：

```
get_pod()

get_logs()

query_metric()

query_trace()
```


---

## Write Tool


需要审批。


例如：

```
restart_pod()

scale_deployment()

rollback()
```


---

# 14. Tool 与 Evidence 关系


完整流程：


```
Tool 调用

    |

    v

Raw Response             ← 各 MCP Server 返回

    |

    v

Evidence Builder         ← Tool Layer 统一组件
    |                       （转换规则集中管理，
    |                        不分散在各 MCP Server）

    v

Agent State.evidence

    |

    v

Reasoning Loop

```


---

# 15. 示例流程


用户：

```
为什么 Pod 一直重启？
```


Workflow 调用：


```
get_pod()

    |

    v

PodStatus Evidence


    |

    v


get_events()

    |

    v

OOMKilled Evidence


    |

    v


query_metric()

    |

    v

Memory Usage Evidence

```


最终：


```
Evidence集合

        |

        v

Agent分析

        |

        v

RCA输出

```


---

# 16. 后续扩展


未来增加：


```
Database Tool

Network Tool

Cloud Provider Tool

Security Tool

```


---

# 17. 当前设计结论


Tool Layer 负责：


```
连接基础设施

获取数据

转换 Evidence

提供安全能力
```


MCP 负责：

```
标准化 Tool 调用协议
```


Agent 负责：

```
如何使用这些能力解决问题
```


---

# 18. 当前开发路线


阶段一：

完成 Kubernetes Tool。


目标：


```
get_pod()

get_events()

get_logs()

```


然后实现：

```
Pod CrashLoopBackOff Workflow

```


验证：

```
Agent

+

Workflow

+

State

+

MCP

+

Tool

```

完整闭环。
