# 协议：消息 Envelope、发布订阅、命令与 ack

本文定义平台所有 Kafka 消息的统一 envelope 与协作规则。是 `backend/anp/contracts` 的实现依据。命名见 [naming.md](naming.md)，状态语义见 [world-status.md](world-status.md)。

## 1. 统一 Envelope

每条 Kafka 记录是一个 UTF-8 JSON envelope。**所有层、所有消息共用同一 envelope 外壳**，差异只体现在 `event_type` 与 `payload`。

```jsonc
{
  "schema_version": "1.0",
  "message_id": "<全局唯一，建议 uuid4 派生>",
  "event_type": "<点分类型，见 §3>",
  "source": {
    "system": "collaborative_agent | platform",
    "agent_id": "<发送方 agent id，见 naming.md>",
    "gateway_id": "<可选，经网关时填>"
  },
  "target": {                       // 上行观测/状态留空 {}；命令必填 agent_id
    "agent_id": "<可选>",
    "region_id": "<可选>"
  },
  "time": {
    "event_ts": "<ISO8601 UTC，带 Z；权威物理/事件时间>",
    "sequence": 0,                  // 发送方单调递增序号
    "expires_at": "<可选，ISO8601 UTC；命令用，过期拒收>"
  },
  "scope": {
    "site_id": "<站点/区域>",
    "region_id": "<逻辑区域>",
    "object_id": "<可选，被描述实体，如 intersection_id>"
  },
  "payload": { /* 层/事件相关，schema 见 schemas/ */ },
  "quality": { "confidence": 1.0, "data_latency_ms": 0 },
  "trace": { "trace_id": "<贯穿一次因果链>", "parent_trace_id": "<可选>" }
}
```

字段固定，不得各处另起名或私自加层级。`payload` 的具体结构按 topic 分别定义，并在 `schemas/` 落 JSON Schema。

## 2. 时间语义（关键）

- `event_ts` 是**权威事件时间**（物理世界发生时刻），不是写入 Kafka 的时刻。
- 系统级智能体的滚动窗口**按 `event_ts` 切桶**，不按 ingest 时间，以此对齐网络延迟与乱序。
- 迟到消息：若 `event_ts` 早于当前已关闭窗口的水位线，直接丢弃（见 world-status.md 的 grace 规则）。
- 时间一律 UTC、ISO8601、带 `Z`。

## 3. event_type 与 payload 对应

| topic | event_type | payload 关键字段 |
|---|---|---|
| `anp.traffic.perception.observation.v1` | `observation.traffic.intersection` | `intersection_id`、`approaches[]`（见 world-status.md §2） |
| `anp.traffic.status.intersection.v1` | `status.traffic.intersection` | 路口 World Status（见 world-status.md §3） |
| `anp.traffic.command.v1` | `command` | `command_id`、`command_type`、`params{}` |
| `anp.traffic.ack.v1` | `command.ack` | `command_id`、`command_type`、`status`、`safety{}` |
| `anp.traffic.agent.lifecycle.v1` | `agent.registered` / `agent.deregistered` | `agent_id`、`agent_type`、`capabilities[]`、`command_types[]` |
| `anp.traffic.agent.heartbeat.v1` | `agent.heartbeat` | `status`、`last_error`、角色相关健康字段 |

## 4. 发布订阅规则

1. 每条记录一个 envelope。**Partition key = 实体 id**（多为 `source.agent_id`；状态层用 `object_id`，如 `intersection_id`），保证同实体有序、便于窗口聚合与回放。
2. 生产者必须填 `message_id`（唯一）、`event_ts`（事件时间）、`sequence`（单调递增）。
3. 消费者必须做 schema 校验；不合法消息跳过并计数，可选写入 DLQ（`anp.traffic.dlq.v1`，本期预留、暂不强制）。
4. 不要每个 agent 一个 topic；同层同类共用 topic，用 partition key 与 payload 字段区分实体。

## 5. 命令与 ack 生命周期

下行命令走 `anp.traffic.command.v1`，回执走 `anp.traffic.ack.v1`。

**命令 payload：**

```jsonc
{ "command_id": "<唯一>", "command_type": "set_signal_plan | ...", "params": { /* 命令相关 */ } }
```
`time.expires_at` 必填（网关按 `expires_in_sec` 计算）。`target.agent_id` 必填，禁止 broadcast。

**执行端处理顺序（权威 Safety Guard 在执行端，不在网关）：**

1. **去重**：`command_id` 已处理过 → 回 ack `status="duplicate"`，不重复执行。
2. **过期**：`expires_at < now` → `status="expired"`，不执行。
3. **目标匹配**：`target.agent_id` 不是自己 → 忽略。
4. **本地 Safety Guard**：命令类型白名单、参数范围（如 `set_signal_plan` 的 duration）、GUI/DB 等约束。不通过 → `status="rejected"` 并附 `safety.reason`。
5. 通过 → 执行 → 回 ack（`accepted` 或 `completed`）；执行失败 → `status="failed"`。

> 实现注（v1，`agents/virtual_traffic.py:VirtualTrafficExecutor`）：共享命令 topic 上，执行端可把
> 「目标匹配」**上提到去重之前**——非本体命令直接忽略（不回 ack、不进本体去重表），避免别的执行体的
> `command_id` 污染本体去重表。对寻址到本体的命令，处理顺序与上面一致，观测语义不变。去重表为内存集合，
> 但可由重放本体既往 ack（`rebuild_dedup_from_acks`）重建，满足 §6「去重表应可重建」。

**ack status 枚举**：`accepted | completed | rejected | duplicate | expired | failed`。

**ack payload：**

```jsonc
{
  "command_id": "<回指>",
  "command_type": "...",
  "status": "accepted | completed | rejected | duplicate | expired | failed",
  "safety": { "allowed": true, "decision": "...", "reason": "..." }
}
```

## 6. 幂等、乱序、重复、过期（实现必须考虑）

- **幂等**：执行端按 `command_id` 去重；去重表应可持久化或可重建（不要像老仓库只放内存，进程重启即丢）。
- **乱序**：靠 `event_ts` + `sequence` + partition key；窗口按事件时间聚合，容忍迟到到 grace 上限。
- **重复投递**：消费侧按 `message_id` / `command_id` 幂等。
- **过期**：命令带 `expires_at`；观测无需过期字段，但迟到超窗丢弃。

## 7. 安全边界

- 平台负责协调与审计，**不替代执行端本地 Safety Guard**。
- 网关只校验命令外形（必填 `target_agent_id`、命令类型合法、禁止 `broadcast`/`agent_ids`）+ 白名单，不做业务安全判定。
- 白名单：谁能发布/订阅哪些 topic 由平台侧统一配置（registry，见 architecture.md）。
