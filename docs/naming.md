# 命名规范：逻辑 World Model / 物理 Topic / 智能体

统一命名是多域隔离与运维的基础，也用来终结老仓库 `agent-network` / `world-model` 双轨命名。前缀统一用 **`anp`**（Agent Network Platform）。本规范是硬约束，新增 topic、agent、状态都必须遵守。

## 1. 逻辑 World Model（协同任务域）

一个协同任务域 = 一个 World Model，用一个简短的**域名（domain）**标识，小写、单词、无连字符：

| 域名 | 含义 | 状态 |
|---|---|---|
| `traffic` | 交通管控 World Model | 本期落地 |
| `mine` | 矿区安全管控 World Model | 预留，本期不做 |
| `video` | 视频监控 World Model（事件文本问答） | 本期落地（P7，见 [video.md](video.md)） |

逻辑 World Model 的展示名（如「交通管控 World Model」）只用于 UI 与文档，不进 topic / id。

## 2. 物理 Topic

格式：

```
anp.<domain>.<layer>.<name>.v<major>
```

- `<domain>`：域名，见上。
- `<layer>`：固定层枚举，见下表。
- `<name>`：该层下的具体信号/状态/事件名，小写下划线或点分。
- `v<major>`：schema 主版本，从 `v1` 起；不兼容变更才升主版本。

| layer | 含义 | 生产者 → 消费者 |
|---|---|---|
| `perception` | 感知层：原始观测、状态镜像 | 感知智能体 → 系统级智能体 |
| `status` | 状态层：语义化 World Status | 系统级智能体 → 任务/执行智能体、网关 |
| `command` | 控制层：下行命令 | 网关/决策方 → 执行智能体 |
| `ack` | 控制层：命令回执 | 执行智能体 → 网关/审计 |
| `agent.lifecycle` | 注册/上线/下线 | 各智能体 → registry |
| `agent.heartbeat` | 心跳/在线状态 | 各智能体 → registry |

### 交通域 v1 Topic 清单

```
anp.traffic.perception.observation.v1     # 路口各方向车辆数/滞留/速度等原始观测
anp.traffic.status.intersection.v1        # 系统级智能体产出的路口 World Status
anp.traffic.control.phase.v1              # 控制层相位注入（执行体→SV 写灯口，per-junction phase_index，task5）
anp.traffic.command.v1                    # 下行命令（如信号配时）
anp.traffic.ack.v1                        # 命令回执
anp.traffic.agent.lifecycle.v1            # 智能体注册/上下线
anp.traffic.agent.heartbeat.v1            # 智能体心跳
```

原则：**不要每个 agent 一个 topic**。同一层、同一类信号共用 topic，用 partition key 与 payload 字段区分实体。

### 视频域 v1 Topic 清单（P7/P8）

```
anp.video.perception.text.v1              # 视频大模型处理后的文本事件（原始视频不进 Kafka）
anp.video.command.v1                      # P8：请求视频推理命令（ANP→vision hub，经 adapters/visionhub 译出）
anp.video.dlq.v1                          # 预留
```

P7 一阶段只用感知层（文本事件上行）。**P8 补控制层** `anp.video.command.v1`——ANP 下发「请求视频推理」命令，经 `adapters/visionhub` 译给 vision hub，结果文本回流仍走 `anp.video.perception.text.v1`（闭合对称双向环；vision hub 原生 topic 不归本命名规范管，由 adapter 内部对接）。视频域仍**无状态层**（无共识聚合）。详见 [video.md](video.md) §10、[adapters.md](adapters.md) §5。

## 3. Partition Key

分区键统一用**实体 id**（多数情况是 `agent_id`，状态层用被聚合的物理实体 id，如 `intersection_id`），保证同一实体的消息有序，便于窗口聚合与回放。

## 4. 智能体 ID

格式：

```
<domain>-<role>-<seq>
```

- `<role>` ∈ `perception` | `system` | `task` | `exec`。
- `<seq>`：三位序号或简短稳定标识。

示例：

```
traffic-perception-001    # 感知智能体
traffic-system-001        # 系统级智能体
traffic-exec-signal-001   # 信号配时执行智能体
```

v1 虚拟交通智能体同时承担感知与执行，统一登记为 `traffic-virtual-001`，能力声明里标注 `perception` + `exec`。

SignalVision 感知接入适配器（P5，见 [adapters.md](adapters.md)）作为纯感知源登记为 `traffic-perception-sv-001`，能力 `perception`，`command_types` 为空。

SignalVision 信号控制执行体（P6/B-6/B-10，见 [adapters.md](adapters.md) §3）登记为 `traffic-exec-sv-001`，能力 `exec`，`command_types=[set_signal_plan, control_signal_inference, set_signal_map]`（细粒度相位、写展示层不驱动 SUMO §3.4；粗粒度启停/选算法、真驱动 SUMO §3.5；全局换图 §3.6）。

视频域（P7，见 [video.md](video.md)）：视频感知体 `video-perception-001`（role=perception，发文本事件）；视频问答任务体 `video-task-001`（role=task，检索+合成）。

vision hub 双向桥（P8，见 [adapters.md](adapters.md) §5）：出口桥源身份 `video-visionhub-bridge-001`（写在译出的 vision hub info 消息上）；结果回流后在 ANP 侧重新发布文本事件的**感知体**身份 `video-perception-visionhub-001`（role=perception，镜像 `traffic-perception-sv-001`）；远端 vision hub 推理体逻辑 ID `video-visionhub-001`（命令 target，step1 由替身脚本扮演）。

## 5. World Status 实体与状态名

状态层每条记录描述一个物理实体的语义状态，实体 id 命名稳定可复用（如路口 `intersection_id`）。具体状态字段（排队长度、流量等）的精确定义见 world-status.md。

## 6. Envelope 字段命名

所有消息共用一个 envelope（精确定义见 protocol.md），关键字段固定命名：`schema_version`、`message_id`、`event_type`、`source`、`target`、`time`（含 `event_ts`、`sequence`、可选 `expires_at`）、`scope`、`payload`、`quality`、`trace`。各处不得另起字段名或私自加层级。
