# 世界时钟（World Clock）v1 — 多源感知的统一时间契约

> 状态：v1 已落地（2026-06-30）。轻版——**时间契约 + 新鲜度工具**，不做应用层跨机时钟偏移
> 校正（跨机靠 NTP，见 §6）。这是**多源感知接入的前置**：视频组摄像头智能体接入前必须遵守本契约。

## 1. 为什么需要

ANP 是多源黑板：感知源有 SUMO 适配器、将来视频组摄像头、再将来真实路口设备。它们各自有时钟——
SUMO 是**仿真时钟**（`sim_time`/`sim_step`），摄像头/真实设备是**真实挂钟**。要让交通智能体把多源
观测对齐到同一条时间轴、判断"这条路口信息是几秒前的、还能不能用来决策"，必须有**统一世界时基**。

第一步只有 SUMO 单源时用 `sim_step` 凑合判过期；多源接入后 `sim_step` 失效（摄像头没有"仿真步"），
所以引入世界时钟 v1。

## 2. 统一世界时基 = UTC 挂钟

- 所有边缘智能体以 **UTC 挂钟** 为共同时基。
- 每条消息的 `envelope.time.event_ts` = **权威事件时刻**（该观测对应的**真实世界时刻**，**不是发送时刻**），
  ISO8601 UTC、带 `Z`、毫秒精度（如 `2026-06-30T08:00:05.000Z`）。
- `sim_clock`（仿真时钟）**降级为 SUMO 源的辅助元数据**，不再作跨源主判据。视频组等真实源**不带** `sim_clock`。

## 3. 时间字段与"挂钟贯穿全链"

权威事件时刻从感知一路贯穿到执行，让执行端能算"决策基于的观测有多旧"：

| 层 | 消息 | 事件时刻所在 |
|---|---|---|
| 感知 | `observation.traffic.intersection` | `envelope.time.event_ts`（感知源填真实事件时刻）|
| 状态 | `status.traffic.intersection` | `envelope.time.event_ts`（model **透传**观测的 event_ts，不重写）|
| 控制 | `control.traffic.phase` | `payload.based_on_event_ts`（执行体把所基于状态的 event_ts **透传**进来）|

执行端（SV 写灯口）拿到 `based_on_event_ts`，即可算新鲜度。

## 4. 新鲜度 / 过期判据（统一挂钟）

- `age = now − origin_event_ts`；`fresh = (age ≤ max_age)`，`max_age` 默认 **12s**（视频 5s 间隔留 ~2 个间隔抖动）。
- 工具（`backend/anp/contracts/clock.py`，已从 `anp.contracts` 导出）：
  - `age_seconds(origin_event_ts, now=None) -> float`
  - `is_fresh(origin_event_ts, max_age_sec=DEFAULT_MAX_AGE_SEC, now=None) -> bool`（缺失/非法/未来时刻保守判不新鲜）
  - `now_utc()`、常量 `DEFAULT_MAX_AGE_SEC`
- SV 写灯口（`adapter/libsignal_adapter.py::_anp_override_action`）：
  - **主判据 = 挂钟新鲜度**：`based_on_event_ts` 存在则 `age > ANP_MAX_AGE_SEC(默认12)` → 过期回落内置算法。
  - **SUMO 源旁路**：`ANP_MAX_LAG_STEPS(默认30) > 0` 且带 `based_on_sim_step` 时，叠加 `sim_step` 落后判据
    （防 SUMO 加速下注入"仿真上已过时"的相位；真实源不带 `sim_step`、自动跳过此旁路）。
  - 两个时间基都缺失 → 保守回落。

> 为什么主判据是挂钟:实测端到端挂钟延迟亚秒级(~300ms),「秒级跟上」轻松达标;SUMO 全速(step_delay=0)
> 全过期的真因是仿真被加速、非延迟,真实世界不加速无此问题(见 `tasks/task5/followups.md` C-2)。

## 5. ★ 给视频组的接入契约

视频组作为**感知智能体**接入（做 CV、只把结构化路口信息发进 ANP，原始视频不进 Kafka）：

- **topic**：`anp.traffic.perception.observation.v1`
- **频率**：每路口每 **5s** 发一次（可配；执行端 `max_age=12s` 容忍约 2 个间隔）。
- **event_ts**：填该 5s 窗口的**代表时刻**（建议窗口结束时刻），UTC ISO8601 带 `Z`、毫秒。
- **payload**（`ObservationPayload`，方向级；**不带** `sim_clock`）：

```json
{
  "schema_version": "1.0",
  "message_id": "<uuid>",
  "event_type": "observation.traffic.intersection",
  "source": {"system": "collaborative_agent", "agent_id": "traffic-perception-cam-<jid>", "gateway_id": null},
  "target": {"agent_id": null, "region_id": null},
  "time": {"event_ts": "2026-06-30T08:00:05.000Z", "sequence": 0, "expires_at": null},
  "scope": {"site_id": null, "region_id": null, "object_id": "<intersection_id>"},
  "payload": {
    "observation_type": "traffic.intersection",
    "intersection_id": "<intersection_id>",
    "approaches": [
      {"direction": "north", "vehicle_count": 8, "halting_count": 5, "mean_speed_mps": 6.2, "mean_delay_sec": null},
      {"direction": "south", "vehicle_count": 6, "halting_count": 3, "mean_speed_mps": 7.1, "mean_delay_sec": null},
      {"direction": "east",  "vehicle_count": 4, "halting_count": 2, "mean_speed_mps": 8.0, "mean_delay_sec": null},
      {"direction": "west",  "vehicle_count": 5, "halting_count": 4, "mean_speed_mps": 5.5, "mean_delay_sec": null}
    ]
  },
  "quality": {"confidence": 1.0, "data_latency_ms": 0},
  "trace": {"trace_id": "<uuid>", "parent_trace_id": null}
}
```

字段语义（对交通智能体决策的作用）：
- `direction` ∈ `north|south|east|west`：进口方向（畸形交叉口按最近罗盘方向归并）。
- `halting_count`：该方向**停车/排队**车辆数 —— **执行体 max-pressure 的主输入**（× 车均长 7m 估排队压力），最关键。
- `vehicle_count`：该方向当前/窗口内车辆数（第一步语义=瞬时车数，非吞吐，见 followups A-2）。
- `mean_speed_mps`：平均速度（m/s）。
- `mean_delay_sec`：可空，缺省由系统级推导。
- `intersection_id`：须与 ANP 世界名册/路网中的路口 id 对齐（接入前与 ANP 侧约定 id 映射）。

注册（统一世界名册，每路口一个感知 agent 身份）：
- 发 `agent.registered` 到 `anp.world.agent.lifecycle.v1`（`agent_type` 自定、`capabilities=["perception"]`、
  `produces` 通道 `topic=观测topic, keys=[intersection_id]`），并周期发 `agent.heartbeat` 到 `anp.world.agent.heartbeat.v1`。
- 可参照 SV 侧实现 `dashboard/integration/anp_kafka.py`（`AnpProducer`/`AnpRegistrar`/`observation_envelope`）——
  同样**不 import anp 包**、按本契约手工构造 envelope dict 即可。

## 6. 部署要求（跨机）

- 视频组很可能在**另一台机器**。轻版 v1 **不做**应用层时钟偏移校正,**依赖各机 NTP 同步**(同机房 NTP 通常已足够准)。
- 接入机与 ANP 机都应启用 NTP；若两机时钟偏差可能 > 1~2s，再议是否升级到 §7 的应用层校正。

## 7. 当前边界 / 未来

- **已做**：统一挂钟时基语义、`event_ts` 贯穿全链、新鲜度工具、SV 写灯口挂钟过期判据（`sim_step` 降为旁路）。
- **未做（v1 轻版边界）**：应用层跨机时钟偏移估计/校正、回放/跨边缘对齐、统一时钟源服务。触发=多机偏差大 / 回放需求 / 多边缘。

相关：契约源 `backend/anp/contracts/{clock.py,payloads.py}`；过期判据 `adapter/libsignal_adapter.py`；
task5 与 followups D-1（本模块即其落地）。
