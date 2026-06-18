# 感知接入适配器（Adapters）

本文定义外部真实数据源如何接入平台感知层，是 `backend/anp/adapters/` 的实现依据。
Envelope 契约见 [protocol.md](protocol.md)，观测 payload 见 [world-status.md](world-status.md) §2，
topic/agent id 见 [naming.md](naming.md)。

## 1. 适配器是什么 / 不是什么

适配器把**某个外部数据源的原生结构**映射成 `anp.contracts` 的统一契约：感知侧映射成
**按方向原始观测**发布到感知层 topic；执行侧把契约命令映射成该源的控制调用并回 ack。
对平台而言它就是一个感知 / 执行智能体。

- **做**：HTTP/SDK 接入外部源、字段与单位映射、按契约装配观测、注册/心跳/下线；
  （执行侧）订阅命令 → 去重/过期/目标匹配/本地 Safety Guard → 调外部源控制接口 → 回 ack。
- **不做**：在适配器里散搓 envelope（一律走 `observation_envelope` / `ack_envelope`）；计算
  World Status（排队/流量/延误/拥堵等共识指标一律由系统级智能体算，AGENTS.md §3.2）。

每个外部源一个子包，互不耦合。当前落地：`signalvision/`——**感知侧**（§2）+ **执行侧**
（§3 信号控制，P6），同子包、职责分离、共用 client。

## 2. SignalVision 感知适配器

### 2.1 数据源

真实 SV Dashboard（`~/project/SignalVision/dashboard/server.py`）暴露 HTTP API。
**感知侧**只用只读端点（写端点见 §3 执行侧）：

| 端点 | 用途 |
|---|---|
| `GET /api/simulation/status` | 仿真运行状态 → 心跳携 SV 可达性 |
| `GET /api/junctions/<junction_id>` | 单路口完整状态（`incoming_lanes` / `metrics` / `traffic_light`）→ 映射观测 |

SV 单路口状态关键字段（见 `integration/junction_agent.py:get_state_dict`）：

```jsonc
{
  "junction_id": "intersection_1_1",
  "incoming_lanes": {                       // 进口车道，逐车道
    "<lane_id>": { "vehicle_count": 8,      // 瞬时在道车数
                   "mean_speed": 3.2,        // m/s（SUMO 原生）
                   "halting_count": 5 }      // 瞬时滞留
  },
  "metrics": { "total_vehicles_passed": 1200 }  // 累计通过量（单调递增）
}
```

### 2.2 契约鸿沟与映射决策（关键）

SV 的原生结构与平台契约（[world-status.md](world-status.md) §2 的 `intersection_id` +
按罗盘方向 `approaches[]`）有三处不一致，适配器逐一桥接：

1. **方向**：SV 的 lane `direction` 只有 `incoming`/`outgoing`，**没有罗盘方向**；契约
   `Approach.direction ∈ {north,south,east,west}`。
   → 适配器把**进口车道**按策略归并成至多 4 个罗盘方向 approach：
   - `auto`（默认）：先从 `lane_id` 抽罗盘 token（`north`/`北`/边界单字母 `n`…），抽不到
     再按排序序号轮询 N/S/E/W；
   - `round_robin`：一律按序号轮询。
   - **取舍**：当 `lane_id` 不编码地理方向时，罗盘标签是 v1 启发式分配；但系统级聚合按
     方向对称（路口级 queue/flow/speed/delay 是各方向汇总），故**路口级 World Status 正确**，
     仅单方向标签为名义值。真实接入若已知车道几何，覆盖策略或预映射即可得真方向。

2. **通过量**：契约 `vehicle_count` 是「采样间隔内**通过**量」（吞吐，用于换算流量）；SV 的
   lane `vehicle_count` 是**瞬时在道车数**。直接塞瞬时数会让下游 `flow_veh_h` 虚高一个量级。
   → 适配器用 junction `metrics.total_vehicles_passed`（累计）**轮询差分**得到本间隔通过量，
   再按各方向瞬时车数占比**整数分摊**（最大余数法，和恰为差分值）。
   - 首轮无基线 → 通过量记 0；计数器回退（SV 重启）→ 视为从 0 重计，取当前值。

3. **延误**：`mean_delay_sec` **留空**，交系统级智能体按速度推导——适配器不算 World Status。

逐方向映射：

| 契约字段 | 来源 |
|---|---|
| `approaches[].direction` | 进口车道按策略归并的罗盘方向 |
| `approaches[].halting_count` | 该方向各进口车道 `halting_count` 之和 |
| `approaches[].mean_speed_mps` | 各车道 `mean_speed` 按瞬时车数加权均值（无车数则算术均值） |
| `approaches[].vehicle_count` | junction 间隔通过量按方向瞬时车数占比整数分摊 |
| `approaches[].mean_delay_sec` | 留空（系统级推导） |

> SV 的 `traffic_light.phase_state`、junction `congestion_level` 等**不进感知观测**：前者属
> 控制侧信息（契约观测无对应字段，本期不做），后者是 SV 自算的共识指标（平台共识指标由系统级
> 智能体算，避免双算分叉）。

### 2.3 身份与发布

- agent_id：`traffic-perception-sv-001`（[naming.md](naming.md) §4，role=`perception`）。
- agent_type：`signalvision`；capabilities：`["perception"]`；command_types：`[]`（纯感知）。
- 观测发布到 `anp.traffic.perception.observation.v1`，`event_type=observation.traffic.intersection`，
  `scope.object_id=intersection_id`，`quality.confidence` 默认 0.95（系统级 `MIN_CONFIDENCE=0.3` 过滤）。
- 心跳发布到 `anp.traffic.agent.heartbeat.v1`：SV 可达 `online`，不可达 `degraded` 并附 `last_error`。
- 启动注册 / 退出下线走 `anp.traffic.agent.lifecycle.v1`。

默认把 SV junction `intersection_1_1` 映射到平台 `gg-xiongchu-minzu`，使本适配器作为感知源时
端到端与 v1 虚拟感知体一样点亮网关同一路口；真实接入按 SV 实际 junction_id 用 `--junction/--intersection`
或 `junction_map` 覆盖。

### 2.4 模块（`backend/anp/adapters/signalvision/`）

| 文件 | 职责 |
|---|---|
| `config.py` | `SignalVisionAdapterConfig`：agent_id、SV 地址、轮询间隔、`junction_map`、方向策略、置信度 |
| `client.py` | `SignalVisionClient`：SV 只读 HTTP（标准库 urllib，不引新依赖） |
| `mapping.py` | 纯映射：方向归并、通过量差分、整数分摊 → `ObservationPayload`（可单测） |
| `service.py` | `SignalVisionAdapter`：poll→map→publish 一轮 + 循环；lifecycle/heartbeat envelope |

### 2.5 运行与验证

```bash
# 运行（接真实 SV Dashboard；配合 run_system_agent.py + run_gateway.py 端到端）
python backend/scripts/run_signalvision_adapter.py --sv-base-url http://127.0.0.1:8080 \
    --junction intersection_1_1 --intersection gg-xiongchu-minzu
# 端到端冒烟（桩 SV + 样例回放 → adapter → Kafka → 系统级 World Status；需 Kafka）
python backend/scripts/smoke_signalvision_adapter.py
# 单测（映射/差分/分摊/契约/客户端/降级，无 Kafka）
cd backend && pytest tests/test_signalvision_adapter.py -q
```

**已验证**：单测全绿；桩 SV 端到端冒烟 PASS（按方向观测经 Kafka 两 topic 产出 3 窗口
World Status）；run 脚本注册/降级/下线接线正常。
**未验证风险**：未对接**真实** SV Dashboard 实测（本机未跑、启动要拉 SUMO，属独立重项目）；
真实 `lane_id` 是否编码罗盘方向、`total_vehicles_passed` 单调性等需真实接入时复核。

## 3. SignalVision 执行 adapter（信号控制，P6）

感知侧的对偶：把契约下行命令映射成 SV 控制调用，完成「命令下行 → 执行 → ack」闭环。

> **纠正**：P5 文档曾把后续控制写成 `sv.inference.start/stop/status/snapshot`——这是占位
> 概念，**真实 SV 无此端点**。真实可用的信号控制入口是下面的 `POST .../update`。

### 3.1 数据源（写端点）

| 端点 | 用途 |
|---|---|
| `POST /api/junctions/<junction_id>/update` | 写 `traffic_light{phase_state, phase_duration, next_switch_time}`（信号控制）/ `lane_data` |

### 3.2 命令语义与映射（单相位覆盖，P6）

复用既有 `set_signal_plan` 命令（**契约零改**），params 形态 `{desired_phase, duration_s}`：

| 契约 params | SV `/update` traffic_light | 说明 |
|---|---|---|
| `desired_phase`（符号名，如 `north_south_green`） | `phase_state` | 经 `phase_state_map` 可覆盖为 SV 相位串，默认透传符号名 |
| `duration_s` | `next_switch_time` | 本相位 `duration_s` 秒后切换 |
| —（新设相位） | `phase_duration = 0.0` | 当前相位已持续 0 |

- **目标路由**：命令 `scope.object_id`（intersection_id）经 `junction_map`（与感知侧同向：
  SV junction → 平台 intersection）**反查**目标 SV junction；未映射 → Safety Guard 拒绝。
- **Safety Guard**：参数级规则（合法相位集合、`duration_s ∈ [5,120]`）来自 `anp.control`
  （单一来源，与 v1 虚拟体共用，避免分叉）；执行体在其上叠加路由约束。权威安全闭环在执行端
  （protocol.md §7）。处理顺序：去重 → 过期 → 目标匹配 → Safety Guard → 路由 → 调 SV → ack。
- **完整多相位配时**（周期/绿信比/相位序列）本遍不做，params schema 留扩展空间。

### 3.3 身份与模块

- agent_id：`traffic-exec-sv-001`（[naming.md](naming.md) §4，role=`exec`）；agent_type `signalvision`；
  capabilities `["exec"]`；command_types `["set_signal_plan"]`。靠 lifecycle 注册（不入 registry seed，
  与感知体一致）。
- `client.py` 加 `update_junction` 写端点；`config.py` 加 `SignalVisionExecConfig`；新增 `executor.py`
  （`SignalVisionExecutor` + exec lifecycle/heartbeat）。
- 命令订阅 `anp.traffic.command.v1`，ack 发布 `anp.traffic.ack.v1`，心跳携 SV 可达性。

### 3.4 运行与验证

```bash
# 运行执行体（接真实 SV Dashboard；配合 run_gateway.py + 感知源端到端跑通命令闭环）
python backend/scripts/run_signalvision_exec.py --sv-base-url http://127.0.0.1:8080 \
    --junction intersection_1_1 --intersection gg-xiongchu-minzu
# 端到端命令闭环冒烟（桩 SV /update + 真实 Kafka command/ack；需 Kafka）
python backend/scripts/smoke_signalvision_exec.py
# 单测（Safety Guard / 映射 / 去重/过期/目标/路由/失败分支，无 Kafka/HTTP）
cd backend && pytest tests/test_signalvision_exec.py -q
```

**已验证**：单测全绿；桩 SV 端到端命令闭环冒烟 PASS（completed/rejected×2/expired/duplicate/ignored
全分支 + SV 写端点收到映射后的 traffic_light）；run 脚本注册/降级（SV 不可达 heartbeat degraded）/下线接线正常。
**未验证风险**：未对接**真实** SV 实测——① `phase_state` 真实编码（SUMO 相位串）需配 `phase_state_map`；
② `/update` 写的是全局 `junction_manager`，**仿真运行时可能不驱动 SUMO**（读详情优先取
`simulation_manager.get_junction_manager()`，server.py:251），故「命令→效果经感知回流」的真实闭环
在 SUMO 上可能合不拢，本遍只验证桩闭环。

## 4. 后续（不在本期）

- 完整多相位配时调度（周期/绿信比/相位序列）；真实 SV/SUMO 闭环实测。
- 视频流迁移、路口预测（会议另两件任务，AGENTS.md §1.4，另开任务）。
- 其余外部源适配器按本文骨架另起子包。
