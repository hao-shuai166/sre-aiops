# Infrastructure Agent State 设计

> Version: v1.0  
> Status: Design Draft

---

# 1. 概述

## 1.1 背景

Infrastructure Agent 在执行故障诊断过程中，需要持续保存：

- 用户请求上下文
- 当前问题判断
- Workflow执行状态
- 已收集证据
- 推理历史
- 最终诊断结果


如果没有统一状态管理，Agent只能：

```
查询数据

↓

立即分析

↓

丢失上下文
```


这种方式无法支持：

- 多步骤诊断
- 反馈循环
- Evidence Driven RCA
- 长流程任务


因此需要设计统一的：

```
Agent State
```

作为 Agent 执行过程中的共享上下文。


---

# 2. Agent State定位


Agent State 不是一个执行步骤。

它不是：

```
Workflow

↓

State

↓

Tool
```


而是：

```
                 Agent State


        +-----------+-----------+

        |           |           |


     Intent     Workflow     Tool

     Node       Node         Node


        |           |           |


        +-----------+-----------+


              Evidence


                 |


              Diagnosis

```


所有节点共享同一个 State。


---

# 3. State设计目标


Agent State需要满足：

## 3.1 上下文连续性

支持：

```
第一次查询

↓

第二次查询

↓

第三次分析
```


Agent始终知道：

- 用户问题是什么
- 已经查过什么
- 当前结论是什么


---

## 3.2 Evidence可追溯


所有判断必须关联：

- 数据来源
- 时间
- 原始内容
- 可信度


---

## 3.3 支持循环推理


支持：

```
分析

↓

发现缺少信息

↓

继续查询

↓

更新State

↓

重新分析
```


---

# 4. Agent State整体结构


```yaml
AgentState:

  # 用户输入

  request:


  # 问题理解

  intent:


  # 当前执行状态

  execution:


  # 证据集合

  evidence:


  # 推理过程

  reasoning:


  # 最终结果

  diagnosis:

```

---

# 5. Request Context


保存用户原始请求。


示例：

```yaml
request:

  user_input:

    "为什么订单服务访问很慢"


  user:

    id:
      user01


  timestamp:

    2026-07-23T10:00:00Z


```

---

包含：

|字段|说明|
|-|-|
|user_input|用户原始问题|
|timestamp|请求时间|
|user|用户信息|

---

# 6. Intent State


保存 Agent 对问题的理解。


示例：

```yaml
intent:


  domain:

    kubernetes


  problem_type:

    pod_failure


  confidence:

    0.92

```


字段：

|字段|说明|
|-|-|
|domain|问题领域|
|problem_type|问题类型|
|confidence|判断置信度|

---

例如：

```
Pod CrashLoopBackOff

↓

domain:

kubernetes


problem_type:

pod_failure

```

---

# 7. Workflow Execution State


保存当前 Workflow 状态。


示例：


```yaml
execution:


  current_workflow:

    pod_diagnosis


  current_step:

    collect_events


  status:

    running


  iteration:

    2

```


字段：


|字段|说明|
|-|-|
|current_workflow|当前Workflow|
|current_step|当前步骤|
|status|执行状态|
|iteration|当前循环次数|

---

# 8. Evidence Model


Evidence 是 Agent 判断问题的基础。


不能简单保存：

```yaml
logs

events

metrics
```


应该结构化。


---

## Evidence结构


```yaml
Evidence:


  id:

    ev001


  type:

    PodEvent


  source:


    system:

      kubernetes


    api:

      events


  timestamp:


    2026-07-23T10:00:00Z


  resource:


    namespace:

      production


    pod:

      nginx-xxx


  content:


    reason:

      OOMKilled


  confidence:


    0.95

```

---

# 9. Evidence字段说明


|字段|作用|
|-|-|
|id|唯一标识|
|type|证据类型|
|source|数据来源|
|timestamp|时间|
|resource|关联资源|
|content|具体内容|
|confidence|可信度|

---

# 10. Evidence类型


第一阶段：

```yaml
EvidenceType:


  Kubernetes:

    - PodStatus

    - Event

    - Log


  Monitoring:

    - Metric

    - Alert


  APM:

    - Trace

    - ServiceMap

```

---

# 11. Reasoning History


保存 Agent 推理过程。


示例：

```yaml
reasoning:


- step:

    1


  observation:

    Pod restartCount持续增加


  conclusion:

    需要查看Events


- step:

    2


  observation:

    Event显示OOMKilled


  conclusion:

    怀疑Memory Limit不足

```

---

作用：

用于：

- 调试Agent
- 分析错误
- 优化Prompt


---

# 12. Diagnosis Result


最终输出。


结构：


```yaml
diagnosis:


  problem:


    Pod CrashLoopBackOff


  root_cause:


    Memory Limit不足


  evidence:


    - ev001

    - ev002


  suggestion:


    Increase memory limit


  confidence:


    0.92

```

---

# 13. Reasoning Loop控制状态


为了避免无限循环。


增加：

```yaml
reasoning_control:


  iteration:

    3


  max_iteration:

    5


  confidence:


    0.85


  need_more_evidence:


    false

```

---

结束条件：

满足任意：

```
iteration >= max_iteration


OR


confidence >= threshold


OR


required evidence satisfied

```

---

# 14. LangGraph State映射


LangGraph中的State：

对应：

```
AgentState
```


节点：

```
Intent Node

↓

Router Node

↓

Workflow Node

↓

Tool Node

↓

Evidence Node

↓

Decision Node

↓

RCA Node

```


每个Node：

读取State

修改State


---

# 15. 示例流程


用户：

```
nginx Pod一直重启
```


---

初始State：

```yaml
request:

 user_input:

  nginx Pod一直重启


```


---

Intent Node更新：

```yaml
intent:

 problem_type:

  pod_failure

```

---

Tool查询：

```yaml
evidence:


- type:

    PodStatus


  content:

    restartCount:15

```

---

继续查询：

```yaml
evidence:


- type:

    Event


  content:

    OOMKilled

```

---

最终：

```yaml
diagnosis:


root_cause:

 Memory Limit不足


confidence:

 0.95

```

---

# 16. 后续扩展


未来支持：

## Memory

保存：

- 历史故障
- 用户习惯
- 集群信息


---

## Knowledge Context

接入：

- SOP
- 文档
- 工单


---

## Multi-Agent共享State


未来：

```
Kubernetes Agent

        |

        v

 Shared Agent State

        |

        v

Monitoring Agent

```

---

# 17. 当前设计结论


Infrastructure Agent State：

负责保存：

```
用户上下文

+

问题理解

+

Workflow状态

+

Evidence

+

推理历史

+

诊断结果

```


它是整个 Agent 系统的核心数据模型。


```