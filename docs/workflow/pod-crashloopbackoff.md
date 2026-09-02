# Pod CrashLoopBackOff Diagnosis Workflow

> Version: v1.0  
> Status: Design Draft  
> Category: Kubernetes Diagnosis Workflow

---

# 1. Overview

## 1.1 Background

`CrashLoopBackOff` 是 Kubernetes 中最常见的 Pod 异常状态之一。

典型表现：

```text
Container启动

↓

Container异常退出

↓

Kubelet尝试重启Container

↓

再次失败

↓

进入CrashLoopBackOff状态
```

当 Pod 进入 CrashLoopBackOff 后，表示 Kubernetes 已经多次尝试启动容器，但是容器持续失败。

常见原因包括：

- 应用启动失败
- 应用配置错误
- ConfigMap / Secret异常
- 依赖服务不可用
- OOMKilled
- 权限问题
- 镜像问题
- 健康检查失败


---

# 2. Workflow目标

## 2.1 输入

用户问题：

```text
为什么 nginx Pod 一直重启？
```

或者：

```text
xxx服务不可用了，帮忙排查
```


---

## 2.2 输出

Workflow最终输出：

```yaml
problem:

  Pod CrashLoopBackOff


root_cause:

  应用启动失败


evidence:

  - Kubernetes Event

  - Container Logs

  - Metrics


suggestion:

  修复配置或者增加资源


confidence:

  0.92
```


---

# 3. Workflow触发条件


该 Workflow 由 Agent Router 触发。


## 3.1 用户意图匹配


以下关键词：

```text
Pod重启

CrashLoopBackOff

Container启动失败

容器不断退出

服务不断重启
```


---

## 3.2 Agent分类结果


```yaml
problem_domain:

  kubernetes


problem_type:

  pod_failure


sub_type:

  crash_loop
```


---

# 4. Workflow总体设计


整体流程：

```text
START

↓

获取Pod状态

↓

获取Events

↓

分析异常类型

↓

选择诊断路径

↓

收集Evidence

↓

Reasoning Loop

↓

生成RCA

↓

END
```


详细流程：

```text
                    START

                      |

                      v

              Get Pod Status
                  [State: evidence += ev001]

                      |

                      v

              Get Kubernetes Events
                  [State: evidence += ev002]

                      |

                      v

              Diagnosis Router
              (conditional_edge)
        /        |        |        \

       v         v        v         v

  OOMKilled   App Err  ImagePull  FailSched  ConfigErr

       |         |        |         |         |

       v         v        v         v         v

 Query Metric  Get Logs  Check Pod  Get Nodes  Get Volumes

       \         \        /         /         /

        \         \      /         /         /

         v         v    v         v         v

              Evidence Collection
              [State: 追加各路径证据]

                      |

                      v

             Decision Node
             (conditional_edge)
                 /       \

               Yes        No
                |          |
                v          v

               RCA     Continue Loop
           [State:       [State: iteration += 1]
          diagnosis]         |
                             v
                         More Tools
                    (回退至 Diagnosis Router
                     或对应诊断分支)
```


---

# 5. Agent State 映射

该 Workflow 运行过程中持续读写 Agent State，与 state-design 的 6 模块完整对应：

| 模块 | 字段 | 本 Workflow 写入内容 |
|------|------|---------------------|
| `request` | `user_input` / `timestamp` | 用户原始问题（如 "nginx Pod 一直重启"） |
| `intent` | `domain` / `problem_type` / `confidence` | domain=kubernetes, problem_type=pod_failure, sub_type=crash_loop |
| `execution` | `current_workflow` / `current_step` / `status` | current_workflow=pod_crash_diagnosis, 每一步更新 current_step |
| `evidence` | `evidence[]` (7 维结构) | 每个 Tool 调用后追加一条 Evidence (id/type/source/timestamp/resource/content/confidence) |
| `reasoning` | `reasoning[]` + `reasoning_control` | 每次 Reasoning Loop 追加推理步骤；control 管控迭代/置信度 |
| `diagnosis` | `problem` / `root_cause` / `evidence_refs` / `suggestion` / `confidence` | RCA 输出时一次性写入 |

> 详细 State 结构参见 [agent-state-design.md](../architecture/agent-state-design.md)。


---

# 6. Step 1: 获取Pod状态


## 6.1 Tool

调用：

```text
get_pod()
```


---

## 6.2 Input


```json
{
  "cluster": "prod",
  "namespace": "default",
  "pod": "nginx"
}
```


---

## 6.3 查询内容


重点字段：

```text
phase

containerState

restartCount

exitCode

reason
```


---

## 6.4 示例结果


```yaml
status:

  Waiting


reason:

  CrashLoopBackOff


restartCount:

  20
```


生成 Evidence：

```yaml
id: ev001

type: PodStatus

source:
  system: kubernetes
  api: pods

timestamp: "2026-07-23T10:00:00Z"

resource:
  namespace: default
  pod: nginx

content:
  status: Waiting
  reason: CrashLoopBackOff
  restart_count: 20

confidence: 1.0
```

### 6.5 State 变化

```yaml
execution:
  current_step: get_pod_status
  status: running

evidence:
  - id: ev001
    type: PodStatus
    ...  # 见上

reasoning:
  - step: 1
    observation: Pod status=Wait, reason=CrashLoopBackOff, restartCount=20
    conclusion: 需要查看 Kubernetes Events 确定退出原因
```


---

# 7. Step 2: 获取Kubernetes Events


## 7.1 Tool


调用：

```text
get_events()
```

### Input

参数从 Step 1 的 Pod 信息中提取：

```json
{
  "cluster": "prod",
  "namespace": "default",
  "resource": "pod/nginx"
}
```


---

## 7.2 查询目标


关注：

```text
OOMKilled

FailedMount

FailedScheduling

ImagePullBackOff

BackOff
```


---

## 7.3 Evidence 示例

```yaml
id: ev002

type: KubernetesEvent

source:
  system: kubernetes
  api: events

timestamp: "2026-07-23T10:00:01Z"

resource:
  namespace: default
  pod: nginx

content:
  reason: OOMKilled

confidence: 0.95
```

### 7.4 State 变化

```yaml
execution:
  current_step: collect_events
  iteration: 1

evidence:
  - id: ev001  # PodStatus (Step 1)
  - id: ev002  # KubernetesEvent (本 Step)
    ...

reasoning:
  - step: 2
    observation: Event reason=OOMKilled
    conclusion: 进入 Memory Diagnosis 路径
```


---

# 8. Diagnosis Router


根据 Evidence 选择诊断方向。


---

# 8.1 OOMKilled


判断：

```yaml
event.reason:

  OOMKilled
```


进入：

```text
Memory Diagnosis
```


需要继续查询：


```text
Prometheus Metrics
```


关注：

```text
container_memory_usage_bytes

container_memory_working_set_bytes

container_memory_limit
```


---

# 8.2 Application Error


判断：

```text
ExitCode != 0

AND

没有OOMKilled事件
```


进入：

```text
Application Diagnosis
```


调用：

```text
get_logs()
```


关注：

```text
ERROR

Exception

panic

connection refused
```


---

# 8.3 ImagePullBackOff


判断：

```yaml
event.reason:

  ImagePullBackOff
```

进入：

```text
Image Diagnosis
```

调用：

```text
get_pod()
# 检查 containerStatuses[*].state.waiting.message
```

关注：

```text
镜像名称是否正确
镜像仓库权限
镜像Tag是否存在
网络连通性
```


---

## 8.4 FailedScheduling


判断：

```yaml
event.reason:

  FailedScheduling
```

进入：

```text
Scheduling Diagnosis
```

调用：

```text
get_events()
get_pod()
```

关注：

```text
节点资源不足
Node Affinity / Taint 不匹配
PVC 无法绑定
拓扑约束
```


---

## 8.5 Configuration Error


例如：

```text
FailedMount

Secret不存在

ConfigMap不存在

权限不足
```

进入：

```text
Configuration Diagnosis
```

调用：

```text
get_events()
get_pod()
# 检查 volumeMounts 和 volumes 配置
```

关注：

```text
ConfigMap 是否存在
Secret 是否存在
ServiceAccount 权限
PVC 绑定状态
```
---

# 9. Step 3: Logs分析


## 9.1 Tool


```text
get_logs()
```


## 9.2 Input

参数从 Agent State 中的 Pod 信息提取：

```json
{
  "cluster": "prod",
  "namespace": "default",
  "pod": "nginx",
  "container": "nginx",
  "tail": 200
}
```


---

## 分析内容


匹配：

```text
ERROR

Exception

panic

permission denied

connection refused
```

### 9.3 State 变化

```yaml
execution:
  current_step: analyze_logs
  iteration: 2

evidence:
  - id: ev001  # PodStatus
  - id: ev002  # Event
  - id: ev003  # ContainerLog (本 Step)
    type: ContainerLog
    source: { system: kubernetes, api: logs }
    content:
      logs: ["connection timeout", ...]

reasoning:
  - step: 3
    observation: 日志多行 connection timeout
    conclusion: 可能是依赖服务不可达，confidence=0.78
```


---

# 10. Step 4: Metrics分析


## 10.1 Tool


```text
query_metric()
```


## 10.2 Input

参数从 Pod 信息提取，时间窗口默认 1 小时：

```json
{
  "cluster": "prod",
  "namespace": "default",
  "query": "container_memory_usage_bytes",
  "time_range": "1h"
}
```


---

## 关注指标


Memory:

```text
container_memory_usage_bytes

container_memory_working_set_bytes
```


CPU:

```text
container_cpu_usage_seconds_total
```

### 10.3 State 变化

```yaml
execution:
  current_step: query_metrics
  iteration: 2

evidence:
  - id: ev001  # PodStatus
  - id: ev002  # Event OOMKilled
  - id: ev004  # Metric (本 Step)
    type: Metric
    source: { system: prometheus, api: query_range }
    content:
      query: container_memory_usage_bytes
      usage: 512Mi / 512Mi (100%)

reasoning:
  - step: 4
    observation: Memory Usage=512Mi/512Mi (100%), Event=OOMKilled
    conclusion: Root Cause = Memory Limit 不足, confidence=0.95
    need_more_evidence: false
```


---

# 11. Evidence Driven Diagnosis


所有结论必须基于 Evidence。


错误：

```text
可能是内存不足
```


正确：

```text
Root Cause:

Container Memory Limit不足


Evidence:

1. Kubernetes Event:

OOMKilled


2. Prometheus:

Memory Usage达到Limit


3. Pod:

Restart Count持续增加
```


---

# 12. Reasoning Loop


CrashLoop诊断不是一次执行完成。


流程：


```text
Collect Evidence

↓

Analyze Evidence

↓

Check Confidence

↓

Enough?

    |

 +--+--+

 |     |

Yes    No

 |      |

RCA   Continue Loop

          |

          v

      Call More Tools

```


---

# 13. Loop终止条件


Reasoning Loop必须有终止限制。


## 13.1 最大循环次数


```yaml
max_iteration:

  5
```


---

## 13.2 置信度阈值


```yaml
confidence_threshold:

  0.85
```


---

## 13.3 Evidence完整性


例如：

OOM问题：

必须：

```text
Event

+

Metric
```


应用异常：

必须：

```text
Pod Status

+

Logs
```


---

# 14. RCA输出规范


统一格式：


```yaml
problem:

  Pod CrashLoopBackOff


root_cause:

  Memory Limit不足


evidence:

  - Kubernetes Event OOMKilled

  - Memory Usage达到Limit


suggestion:

  Increase memory limit


confidence:

  0.95
```

### 14.1 完整 State 转移快照

以 OOMKilled 路径为例，全部 6 个 Step 执行完毕后的 State：

```yaml
request:
  user_input: "nginx Pod 一直重启"
  timestamp: "2026-07-23T10:00:00Z"

intent:
  domain: kubernetes
  problem_type: pod_failure
  sub_type: crash_loop

execution:
  current_workflow: pod_crash_diagnosis
  current_step: rca_output
  status: completed
  iteration: 2

evidence:
  - id: ev001
    type: PodStatus
    source: { system: kubernetes, api: pods }
    content: { status: Waiting, reason: CrashLoopBackOff, restart_count: 20 }
  - id: ev002
    type: KubernetesEvent
    source: { system: kubernetes, api: events }
    content: { reason: OOMKilled }
  - id: ev004
    type: Metric
    source: { system: prometheus, api: query_range }
    content: { usage: "512Mi/512Mi (100%)" }

reasoning:
  - step: 1
    observation: Pod status=CrashLoopBackOff
    conclusion: 需要 Events
  - step: 2
    observation: Event=OOMKilled
    conclusion: 进入 Memory Diagnosis
  - step: 4
    observation: Memory 512Mi/512Mi (100%)
    conclusion: Root Cause = Memory Limit 不足

reasoning_control:
  iteration: 2
  max_iteration: 5
  confidence: 0.95
  need_more_evidence: false

diagnosis:
  problem: Pod CrashLoopBackOff
  root_cause: Memory Limit 不足
  evidence_refs: [ev001, ev002, ev004]
  suggestion: Increase memory limit
  confidence: 0.95
```


---

# 15. 异常处理


## 15.1 Kubernetes API异常


例如：

```text
Timeout
```


处理：

```text
Retry 3 times

↓

失败

↓

结束诊断
```


---

## 15.2 Pod不存在


错误：

```text
PodNotFound
```


处理：

```text
确认namespace

确认pod名称

请求用户补充信息
```


---

## 15.3 Logs获取失败


处理：

```text
跳过Logs

继续其他Evidence
```


---

# 16. LangGraph Node 设计

Workflow 映射到 LangGraph，每个节点标注 LangGraph 类型：

```text
                    START

                      |

                      v

              Intent Node            [function]
              (classify intent →
               set state.intent)

                      |

                      v

              Pod Diagnosis Node     [function]
              (init state.execution)

                      |

                      v

              Get Pod Node           [tool]
              → get_pod()

                      |

                      v

              Get Events Node        [tool]
              → get_events()

                      |

                      v

              Diagnosis Router       [conditional_edge]
         /      |       |       \

        v       v       v       v

  OOM Node    Log     Image   Sched   Config
  [function]  Node    Node    Node    Node
     |       [func]  [func]  [func]  [func]
     |         |       |       |       |
     v         v       v       v       v

  Metric   Log     Pod     Events  Pod
  Node     Anal.   Check   Check   Check
  [tool]   [tool]  [tool]  [tool]  [tool]

     \       \       /       /       /
      \       \     /       /       /
       v       v   v       v       v

          Evidence Update Node     [function]
          (Evidence Builder →
           state.evidence[])

                  |

                  v

          Decision Node            [conditional_edge]
              /       \

             Yes       No
              |         |
              v         v

          RCA Node    Continue Node   [function]
          [function]  (iteration check
          (write      → conditional_edge
           diagnosis)   back to Router
                        或对应分支)
```

**节点类型说明：**

| 类型 | LangGraph 实现 | 示例 |
|------|---------------|------|
| `function` | 普通 Python 函数节点 | Intent Node, OOM Node, RCA Node |
| `tool` | ToolNode (绑定 MCP Tool Manager) | Get Pod Node, Get Events Node, Metric Node |
| `conditional_edge` | `add_conditional_edges()` | Diagnosis Router, Decision Node |

**关键边：**

- `Diagnosis Router → [OOM | Log | Image | Sched | Config]`：根据 `event.reason` 路由，5 路条件分支
- `Decision Node: Yes → RCA Node`：iteration 超限、confidence 达标或 evidence 满足时终止
- `Decision Node: No → Continue Node → Diagnosis Router`：回退至 Router 走新的诊断分支（循环），`max_iteration` 硬限制为 5


---

# 17. Tool依赖


## Kubernetes MCP Server


需要：

```text
get_pod()

get_events()

get_logs()
```


---

## Monitoring MCP Server


需要：

```text
query_metric()
```


---

# 18. MVP实现范围


第一阶段支持：


```text
CrashLoopBackOff

OOMKilled

Application Error
```


暂不支持：


```text
自动修复

自动修改资源

自动Rollback
```


---

# 19. 后续扩展


## 自动修复


支持：

```text
restart pod

scale deployment

modify resource limit

rollback release
```


---

## 知识增强


接入：

```text
SOP

历史故障

工单系统

运维文档
```


---

# 20. 总结


Pod CrashLoopBackOff Workflow 完成完整 AIOps 闭环：


```text
User

↓

Agent

↓

Workflow

↓

MCP Tool

↓

Evidence

↓

Reasoning Loop

↓

RCA
```


该 Workflow 作为 Infrastructure Agent 第一个 Kubernetes Copilot 能力验证流程。