# World Status：交通路口语义状态与窗口聚合

本文定义系统级智能体（`traffic-system-001`）如何把感知观测聚合成路口 World Status，是 `backend/anp/system_agent` 的实现依据。Envelope 见 [protocol.md](protocol.md)。

## 1. 窗口参数

- 类型：**滚动窗口（tumbling）**，固定长度、互不重叠。
- 长度：**10 秒**（`WINDOW_SIZE_SEC = 10`）。
- key：`intersection_id`（每路口独立切桶）。
- 时间基准：`event_ts`（事件时间），不是 ingest 时间。
- 迟到容忍（grace）：窗口在 `window_end + GRACE_SEC` 后关闭并结算；`GRACE_SEC = 2`。结算后再来、`event_ts` 落在已关闭窗口内的消息直接丢弃。
- 异常过滤：schema 不合法、被标记异常的数据源、`quality.confidence` 低于阈值（`MIN_CONFIDENCE = 0.3`）的上报丢弃。

## 2. 输入：感知观测 payload

topic `anp.traffic.perception.observation.v1`，`event_type = observation.traffic.intersection`：

```jsonc
{
  "observation_type": "traffic.intersection",
  "intersection_id": "gg-xiongchu-minzu",
  "approaches": [
    {
      "direction": "north | south | east | west",
      "vehicle_count": 12,      // 本采样间隔内通过的车辆数（吞吐，用于换算流量）
      "halting_count": 5,       // 瞬时滞留（停车）车辆数（用于估排队）
      "mean_speed_mps": 8.3,    // 平均速度 m/s
      "mean_delay_sec": 18.0    // 可选；缺省时由速度推导
    }
  ]
}
```

约定：`vehicle_count` 是**间隔内通过量**（便于流量换算），`halting_count` 是**瞬时停车数**（便于排队估算）。感知体按固定采样间隔上报（v1 虚拟体 2 秒一报）。

## 3. 输出：路口 World Status payload

topic `anp.traffic.status.intersection.v1`，`event_type = status.traffic.intersection`，`scope.object_id = intersection_id`：

```jsonc
{
  "status_type": "traffic.intersection",
  "intersection_id": "gg-xiongchu-minzu",
  "window": { "start": "...Z", "end": "...Z", "size_sec": 10, "sample_count": 5 },
  "queue_length_m": 35.0,
  "flow_veh_h": 180.0,
  "mean_speed_kmh": 29.9,
  "mean_delay_sec": 41.2,
  "congestion_level": "拥堵",        // 畅通 | 缓行 | 拥堵 | 严重
  "congestion_index": 0.62,          // 0..1 连续值
  "approaches": [
    { "direction": "north", "queue_length_m": 14.0, "flow_veh_h": 72.0, "mean_speed_kmh": 26.8 }
  ]
}
```

## 4. 计算规则

设窗口内某路口收到 `N` 条观测（来自各方向、各采样点）。

- **排队长度** `queue_length_m`：取窗口内各方向 `halting_count` 的代表值（按方向取窗口均值后求和或取路口最大方向，v1 用「各方向窗口均值之和」）× 车均占用长度 `VEH_SPACING_M = 7`。
  - 单方向：`queue_length_m[dir] = mean(halting_count[dir]) × 7`。
  - 路口级：`queue_length_m = Σ_dir queue_length_m[dir]`。
- **路段流量** `flow_veh_h`：窗口内通过量换算为每小时。
  - `flow_veh_h = (Σ vehicle_count 窗口内) / WINDOW_SIZE_SEC × 3600`。
- **平均速度** `mean_speed_kmh`：窗口内 `mean_speed_mps` 的（按车辆数加权）均值 × 3.6。
- **平均延误** `mean_delay_sec`：若观测带 `mean_delay_sec` 则取（按通过量加权）均值；否则由速度推导——按名义路段长 `SEGMENT_LEN_M = 200` 的行程时间差：
  - 单方向：`delay[dir] = clamp(SEGMENT_LEN_M/v_obs − SEGMENT_LEN_M/v_free, 0, MAX_DERIVED_DELAY_SEC)`，其中 `v_free = V_FREE_KMH/3.6`（`V_FREE_KMH = 40`），`v_obs` 取该方向窗口加权速度并设下限 `MIN_SPEED_MPS = 0.5`，`MAX_DERIVED_DELAY_SEC = 120`。（注：是「观测行程时间 − 自由流行程时间」，速度越低延误越大。）
  - 路口级：各方向 `delay[dir]` 按通过量加权均值（无通过量时退化为算术平均）。
- **拥堵等级** `congestion_level`（沿用老前端 `HotIntersectionRuntime.state` 档位，按 `mean_delay_sec`）：
  - `≤27` 畅通；`≤38` 缓行；`≤52` 拥堵；`>52` 严重。
  - `congestion_index = clamp(mean_delay_sec / 60, 0, 1)`。

常量集中放 `backend/anp/system_agent`，文档与代码保持一致；调参先改文档。

## 5. 当前态维护与对外

- 系统级智能体在内存维护**每路口最新一条 World Status**（`latest_status[intersection_id]`），供网关 snapshot/projection 直接读，无需回放 Kafka。
- 同时把每个窗口的 World Status 发布到状态层 topic，供任务/执行智能体订阅。
- 字段对齐前端：`flow_veh_h → flow`、`mean_speed_kmh → speedKmh`、`mean_delay_sec → delaySec`、`queue_length_m → queueM`、`congestion_level → state`（见 [gateway-api.md](gateway-api.md) 的节点 metrics 映射）。

## 6. 不在本期

- 不接大语言模型整理状态；不做跨路口/路网级的高阶推理；不落 TimescaleDB（冷路径只留接口）。系统级智能体本期只产出路口级当前态。
