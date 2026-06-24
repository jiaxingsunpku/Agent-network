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

每个外部源一个子包，互不耦合。当前落地：

- `signalvision/`——**感知侧**（§2）+ **执行侧**（§3 信号控制，P6），同子包、职责分离、共用 client。
- `visionhub/`——**vision hub 双向桥**（§5，P8）：命令桥（ANP 视频命令→vision hub info）+ 结果桥
  （vision hub 文本结果→ANP 视频感知层），ANP↔vision hub 双向 Kafka 的翻译边界。

> 适配器的「方向」不止感知/执行：`visionhub/` 是**域级双向桥**——既把 ANP 下行命令译给外部源，
> 又把外部源的文本结果译回 ANP 感知层。本质仍是「只在 adapter 内懂外部原生结构」这一原则。

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
    --junction 1 --intersection gg-xiongchu-minzu   # 真实 SV junction_id 为 1..9（task4 实测）；intersection_1_1 仅旧默认/桩
# 端到端冒烟（桩 SV + 样例回放 → adapter → Kafka → 系统级 World Status；需 Kafka）
python backend/scripts/smoke_signalvision_adapter.py
# 单测（映射/差分/分摊/契约/客户端/降级，无 Kafka）
cd backend && pytest tests/test_signalvision_adapter.py -q
```

**已验证**：单测全绿；桩 SV 端到端冒烟 PASS（按方向观测经 Kafka 两 topic 产出 3 窗口
World Status）；run 脚本注册/降级/下线接线正常。

**真机已验证（2026-06-22，task4 B-0/B-1，消解原未验证风险）**：本机 `~/project/SignalVision`
（conda env `traffic`，Python 3.8，自带 `SUMO_HOME`/libsumo）起 Dashboard 8080、`maxpressure`
预设 SUMO 仿真运行中，接 `run_signalvision_adapter.py --junction 1 --intersection gg-xiongchu-minzu`
→ Kafka → 系统级 → 网关 snapshot 端到端跑通（真实结构、真实零值）。三处真实字段结论：

- **真实 junction_id = `1`..`9`**（非占位 `intersection_1_1`），用 `--junction/--intersection` 校准。
- **`incoming_lanes` 字段名与映射一致**：API `get_state_dict()` 返回 `vehicle_count`/`mean_speed`/
  `occupancy`/`halting_count`，`mapping.py` 直接可用（`vehicle_number`/`halting_number` 仅是 SV
  内部订阅缓存格式、非 API 输出）。
- **`lane_id` 不编码罗盘方向**（SUMO edge 串如 `170316098#3_0`）→ 方向标签为**名义值**（`auto`
  回退 round-robin N/S/E/W）；路口级聚合正确。需真方向须已知几何后预映射。
- **`mean_speed` 单位 m/s**（`speed_limit` 27.78/22.22 = 100/80 km/h），下游按 m/s 正确。
- **`metrics.total_vehicles_passed` 为累计计数器**，差分逻辑可用。
- ⚠️ **零车流 caveat**：guanggu `onfly` demand 实测生成 0 车（仿真步进至 3600 仍 `total_vehicles=0`），
  信号相位照常切换但无车 → World Status 全零，且系统级 `speed=0→delay 120s→严重` 启发式会把**空路**
  误标「严重拥堵」（属系统级解读、非 adapter）。属 SV 场景特性，未改 SV demand。

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
  （protocol.md §7）。处理顺序：**目标匹配（上提）→ 去重 → 过期** → Safety Guard → 路由 → 调 SV → ack
  （目标匹配上提到去重前，避免别体 command_id 污染本体去重表；与 `executor.py:handle_command` 实现及 protocol.md §5 一致）。
- **完整多相位配时**（周期/绿信比/相位序列）本遍不做，params schema 留扩展空间。

### 3.3 身份与模块

- agent_id：`traffic-exec-sv-001`（[naming.md](naming.md) §4，role=`exec`）；agent_type `signalvision`；
  capabilities `["exec"]`；command_types `["set_signal_plan", "control_signal_inference", "set_signal_map"]`
  （= `EXEC_COMMAND_TYPES`；后两者见 §3.5 / §3.6）。靠 lifecycle 注册（不入 registry seed，与感知体一致）。
- `client.py` 加 `update_junction` 写端点；`config.py` 加 `SignalVisionExecConfig`；新增 `executor.py`
  （`SignalVisionExecutor` + exec lifecycle/heartbeat）。
- 命令订阅 `anp.traffic.command.v1`，ack 发布 `anp.traffic.ack.v1`，心跳携 SV 可达性。

### 3.4 运行与验证

```bash
# 运行执行体（接真实 SV Dashboard；配合 run_gateway.py + 感知源端到端跑通命令闭环）
python backend/scripts/run_signalvision_exec.py --sv-base-url http://127.0.0.1:8080 \
    --junction 1 --intersection gg-xiongchu-minzu   # 真实 SV junction_id 为 1..9（task4 实测）；intersection_1_1 仅旧默认/桩
# 端到端命令闭环冒烟（桩 SV /update + 真实 Kafka command/ack；需 Kafka）
python backend/scripts/smoke_signalvision_exec.py
# 单测（Safety Guard / 映射 / 去重/过期/目标/路由/失败分支，无 Kafka/HTTP）
cd backend && pytest tests/test_signalvision_exec.py -q
```

**已验证**：单测全绿；桩 SV 端到端命令闭环冒烟 PASS（completed/rejected×2/expired/duplicate/ignored
全分支 + SV 写端点收到映射后的 traffic_light）；run 脚本注册/降级（SV 不可达 heartbeat degraded）/下线接线正常。

**真机已验证（2026-06-22，task4 B-2，消解执行侧未验证风险）**：真实 SV `maxpressure` 仿真运行中，
起 `run_signalvision_exec.py --junction 1 --intersection gg-xiongchu-minzu`（lifecycle 注册 → 网关
registry 学到 → 可授权），经网关 `POST /api/agent-network/commands` 下发三条、读 ack topic 验真：
`north_south_green/30s @ gg-xiongchu-minzu` → **completed**（SV `/update` 返回 success）；`duration_s=999`
→ **rejected**（Safety Guard）；`object_id=gg-xiongchu-guanggu`（未映射）→ **rejected**（路由约束）。
分支行为与桩一致。`phase_state` 真实编码为 **SUMO RYG 串**（如 `rrrrrrrrrGGG`，r红/G绿/y黄、逐
connection）；因下条结论 `/update` 不驱动 SUMO，`phase_state_map` 配置不影响闭环，留作未来写静态
manager 时按需用。

**核心结论（task4 B-3：`/update` 不驱动 SUMO，命令→效果→感知回流闭环合不拢）**——三重证据：

- **代码**：`server.py` GET `/api/junctions/<id>`(L251) 仿真运行时读 `simulation_manager.get_junction_manager()`
  （= `dashboard_controller.junction_manager`，SUMO 驱动）；POST `/update`(L311) 写的是 server.py
  **模块级全局 `junction_manager`**（另一实例），只调 `agent.update_traffic_light()/update_lane_data()`
  （纯内存 display setter），**不触碰任何 traci/libsumo**。两 manager 不同实例。
- **纯 SV 实验**：仿真运行中 GET J1 = SUMO 驱动 `rrrrrrrrrGGG`（phase_duration 持续推进）；POST `/update`
  写 marker `YYYYYYYYYYYY` 返回 `success:True`，但随后连续 GET 仍是 SUMO 值、marker **从未出现**。
- **ANP 路径复测**：上面 completed 的 `north_south_green` 命令后，GET J1 仍 SUMO 相位、非命令相位。
- **真因**：`/update` 语义是「外部系统向静态/全局 display manager 灌数据」（无仿真时用），从未接线到
  运行中 SUMO 的信号控制。**仿真运行时其写入既不驱动 SUMO、也不反映于 GET（读 sim manager）。**
- **方案建议（曾待用户定）**：① 在 DashboardController 仿真循环里把外部 traffic_light 经
  `traci.trafficlight.setRedYellowGreenState/setPhase` 真正下发 SUMO（需改 SV，最贴近真闭环）；
  ② `/update` 改写 sim 的 `get_junction_manager()` 实例（只让 GET 反映命令、仍不驱动 SUMO，半步）；
  ③ 维持现状，把 SV 定位为「感知源 + 信号展示」，控制闭环留给 SV 自身算法（ANP 不夺真实信号控制权）。
- **用户决策（2026-06-22）**：ANP 的职责是**下发粗粒度命令（如「开始/切换推理」），无需 setPhase 细控**。
  → 不走方案①的 setPhase；改为 §3.5 的**粗粒度控制推理命令**——映射到 SV `/api/simulation/start`/`/stop`，
  **真驱动 SUMO、闭环合得拢**。细粒度 `set_signal_plan`→`/update` 保留为「写展示层、不驱动 SUMO」的对照路径。

### 3.5 控制信号推理命令（`control_signal_inference`，粗粒度、真驱动 SUMO，B-6）

ANP 对 SignalVision 的**主控制命令**：启停 / 选择信号控制算法（推理），而非逐相位下发。复用同一执行体
`traffic-exec-sv-001`（command_types 增列 `control_signal_inference`）。

| 契约 params | 映射 | 说明 |
|---|---|---|
| `action="start"` + `algorithm`（maxpressure/colight/fixedtime/ppo） | `POST /api/simulation/start {"config": algorithm}` | 按所选算法跑信号控制推理，**SUMO 真驱动信号切换** |
| `action="stop"` | `POST /api/simulation/stop` | 停仿真 |

- **Safety Guard**（`anp.control.signal_inference_safety_decision`，单一来源）：`action ∈ {start, stop}`；
  `start` 须带合法 `algorithm`（`ALLOWED_SIGNAL_ALGORITHMS`，仅非 GUI 推理预设）。
- **路由**：仿真级（map 全局）操作，**不需要 object_id→junction 路由约束**（与 `set_signal_plan` per-junction 不同）；
  仅靠 `target_agent_id` 定位执行体。SV 启停失败（如重复 start「仿真已在运行中」）→ ack `FAILED`。
- **前端语义**：控制策略面板只展示“全局算法 + 当前 SV 路网范围”，下发
  `sendAgentNetworkCommand({target_agent_id, command_type:"control_signal_inference", payload:{action, algorithm}})`；
  **不发送目标路口 `object_id`**，避免把全局算法/仿真误呈现成 per-junction 训练或推理。
- **闭环成立**：`start` 让 SUMO 真跑算法 → 信号按算法切换 → 感知 adapter 读回 → World Status 刷新。
  **「命令→效果→感知回流」在此路径合得拢**（与 §3.4 的 `/update` 形成对照）。
- **真机已验证（task4 B-6，2026-06-22）**：live `control_signal_inference{start, maxpressure}` → ack `completed`
  → SV 仿真由 stopped→running（`current_time` 递增）→ 感知回流刷新网关 snapshot；`{stop}` → 仿真停。
  单测（`test_signalvision_exec.py`，启停/越界 action/越界 algorithm/仿真级无 junction/SV 失败）+ 桩冒烟全 PASS。
  注：guanggu 零车流 → World Status 仍全零（演示局限，非闭环缺陷）。

### 3.6 切换路网地图命令（`set_signal_map`，全局换图，B-10）

ANP 指挥 SignalVision **切换活动路网地图**（如 guanggu↔manhattan↔ezhou）。复用同一执行体
`traffic-exec-sv-001`（command_types 增列 `set_signal_map`）。地图是 SV **全局**资源，
**无 per-junction 路由**（与 `control_signal_inference` 同为仿真/全局级）。

| 契约 params | 映射 | 说明 |
|---|---|---|
| `map_path`（相对 SV map 目录，如 `guanggu/netdata.pkl`） | 先 `POST /api/simulation/stop`（best-effort）→ `POST /api/load-map {"map_path"}` | 重建 SV 全局 junction_manager；网关 `/sv-network` 随之返回新路网几何 |

- **Safety Guard**（`anp.control.signal_map_safety_decision`，单一来源）：`map_path` 非空、**禁绝对路径 / `..` 穿越**、
  仅允许 `.pkl` / `.json` 扩展名。非法 → ack `REJECTED`。
- **切图前先停仿真**：执行体先 `stop_simulation()` 再 `load_map`（best-effort，无运行仿真则忽略）。
- **网关只读列图**：`GET /api/agent-network/sv-maps`（relay SV `/api/maps`，与 `/sv-network` 同属「务实例外」，
  只读不入黑板）→ 前端「切换路网」下拉。
- **前端**（`LargeTrafficMapView` + `ToolWorkspace`）：下拉列 `/sv-maps` → 选中 →
  `sendAgentNetworkCommand({command_type:"set_signal_map", payload:{map_path}, target_agent_id: 执行体})` →
  轮询重取 `/sv-network` 重绘；同一份 `/sv-network` 同步驱动“智能体列表”（SV JunctionAgent 列表）、
  “实时交通数据”和“路口指标”，保证这些面板与当前 SV 地图一致。
- **真机已验证（task4 B-10，2026-06-23）**：live `set_signal_map` → ack `completed` → SV 真切图
  （gateway `/sv-network` junction_count guanggu 9 ↔ ezhou 71 反复来回均干净回切）；无头 UI 切图下拉用例 PASS；
  单测（`test_signalvision_exec.py`：正常切图 / 空 map_path / 路径穿越 / 非法扩展名）+ `test_contracts` 共 39 PASS。
- 🔧 **两个 SV 真身坑已就地修补（用户授权，2026-06-23；SV 非 git，已留 `*.anp-bak-*` 备份）**——详见 [[signalvision-update-not-drive-sumo]]：
  ① **切图粘连**：`integration/junction_agent.py::initialize_from_network_data` 重建前不 `clear()` → 多次 load-map 累积旧图路口、切回旧图 `junction_count` 不回落。**补丁：开头加 `self.junctions.clear()`** → 换图干净替换（实测 guanggu 9↔ezhou 71↔manhattan 来回均正确）。
  ② **跑过仿真后路网点位空**：`server.py` 的 `/junctions/summary`、junction-detail 用 `simulation_manager.get_junction_manager()` 非 None 即用、不看 `running`（integration 仿真自结束后仍返回其空 manager）。**补丁：改 `... if simulation_manager.running else 全局 manager`**（2 处）→ 仿真停后几何/点位不再消失。
  ③ **仍存（非代码 bug）**：guanggu `onfly` 零车流 + `inter.incoming_lanes` 为空 → **不起仿真就没 World Status**；要真图 + World Status 兼得需换有真实 demand 的地图。另：SV 的 map 目录与 SUMO 输出目录共用、跑仿真会增删 map（如 manhattan/81 被清、ezhou 出现），故 `/sv-maps` 列表随仿真变动属正常。

## 4. 后续（不在本期）

- 完整多相位配时调度（周期/绿信比/相位序列）；真实 SV/SUMO 闭环实测。
- 路口预测（会议另一件任务，AGENTS.md §1.4，另开任务）。
- 其余外部源适配器按本文骨架另起子包。

## 5. vision hub 双向桥（ANP↔vision hub，P8）

P7 让视频感知体把文本事件**上行**发到 ANP（`anp.video.perception.text.v1`）。P8 补**下行命令**，
让 ANP 主动「请求 vision hub 做一次视频推理」，结果文本回流入库——闭合对称双向环。**只走 Kafka、
只传文本、永不传视频**。设计与 step 划分见 [phases/P8.md](../phases/P8.md)，视频域全景见 [video.md](video.md) §10。

### 5.1 集成形态：ANP 原生契约 + 翻译边界

ANP 内部与命令发起方一律说 **ANP 原生契约**；`adapters/visionhub/` 是**唯一**懂 vision hub 原生
topic/envelope 的地方（镜像 SignalVision adapter）。这样最大化复用 vision hub 已有 Kafka 接口，
**它那边改动最小**（备选「让 vision hub 直接说 ANP 契约」已否决）。

```
ANP 侧（说 ANP 契约）        adapters/visionhub（翻译边界）          vision hub（原生 Kafka）
run_video_command.py
 └─► anp.video.command.v1 ──► [命令桥] ──译──► visionhub.world_model.info.v1
        (request_video_text)                          └─► （step1 替身 / step2 真身）推理产文本
                                                                       │ observation.traffic.video_text
 P7 ingest ◄─ anp.video.perception.text.v1 ◄─ [结果桥] ◄─译─ edge.observation.result.v1 ◄┘
```

### 5.2 vision hub 现有 Kafka 接口（勘察）

| vision hub topic（默认） | 方向 | 用途 |
|---|---|---|
| `visionhub.world_model.info.v1` | 收 | 收世界模型 info（我们注入 `info_type=video_inference_request`）→ step2 补「收→dispatch」胶水 |
| `edge.observation.result.v1` | 发 | 其 canonical adapter 已把 agent run → `observation.traffic.video_text` envelope |

vision hub envelope 约定形如 `{schema_version, message_id, event_type, source, time, scope, payload,
trace{trace_id, correlation_id}}`（aiokafka）。adapter 的 mapping 对其字段**防御性读取**（多备选字段名
+ 缺省兜底）；step2 接真实 repo 时按其当前模块复核字段。

### 5.3 命令语义与映射

复用通用 `CommandPayload`（**仅 `CommandType` 加 `request_video_text` 枚举值**），params 形态：

| 契约 params | vision hub info payload | 说明 |
|---|---|---|
| `camera_id` | `camera_id` | 目标摄像头 |
| `road_name`/`intersection_id`/`road_segment` | 同名 | 路段/路口标识 |
| `time_window{time_from,time_to}` | `time_window` | 推理时间窗（可选） |
| `prompt` | `prompt` | 给视频模型的提问 |
| `clip_ref` | `clip_ref` | 视频片段指针（只传指针不传视频） |
| `command_id`（envelope payload） | `payload.command_id` + `trace.correlation_id` | **关联键** |

结果方向：vision hub `observation.traffic.video_text` 的 payload → ANP `VideoTextEventPayload`，经
`video_text_envelope` 以**感知体身份 `video-perception-visionhub-001`** 重新发布（不冒用 vision hub
内部 agent_id，镜像 SV adapter）；`trace.correlation_id`（= 原 `command_id`）落到 ANP envelope 的
`trace.parent_trace_id`。**P7 ingest/库/QA 零改**入库问答。

- **关联（回执）**：不新增强制 ack topic；用 `command_id`/`correlation_id` 把命令与回流文本关联，
  `CommandTracker` 记「已发→收到结果」（专用 ack 留作后续可选）。
- **Safety**：视频推理请求**非控制动作**，不走信号配时 Safety Guard；vision hub 侧本地有自己的限流/
  安全闭环（step2）。

### 5.4 身份与模块（`backend/anp/adapters/visionhub/`）

| 文件 | 职责 |
|---|---|
| `config.py` | `VisionHubBridgeConfig`：三身份、vision hub topic 名/`info_type`、bootstrap |
| `mapping.py` | 纯映射：ANP 命令→info 消息、vision hub 结果→ANP `VideoTextEventPayload`（防御性、可单测） |
| `command_bridge.py` | `VisionHubCommandBridge`：消费 `anp.video.command.v1`→译→发 vision hub info；记账 |
| `result_bridge.py` | `VisionHubResultBridge`：消费 `edge.observation.result.v1`→译→发 ANP 感知层文本；记账 |
| `tracker.py` | `CommandTracker`：`command_id` 对账表「已发→收到结果」 |
| `admin.py` | `ensure_visionhub_topics`：**仅 step1 本地**幂等创建 vision hub 外部 topic（broker 关了 auto-create） |

身份（[naming.md](naming.md) §4）：出口桥 `video-visionhub-bridge-001`、回流感知体 `video-perception-visionhub-001`、
远端推理体 `video-visionhub-001`。命令源用视频任务体 `video-task-001`（CLI 发起，不经网关/registry，step1 最简）。

### 5.5 运行与验证

```bash
# 端到端命令闭环冒烟（step1：命令→桥→替身桩推理→桥→入库→问答；真实 Kafka，本机两程序）
python backend/scripts/smoke_video_command_loop.py
# live 多进程：双向桥 + 替身 + ingest，再 CLI 发命令（详见 video.md §10.4）
python backend/scripts/run_visionhub_bridge.py
python backend/scripts/stub_visionhub_agent.py
python backend/scripts/run_video_ingest.py
python backend/scripts/run_video_command.py --road-name 民族大道 --prompt "最近有没有事故？"
# 单测（命令/结果纯映射 + 桥 handle + 对账 + 入库往返，无 Kafka）
cd backend && pytest tests/test_visionhub_bridge.py -q
```

**已验证（step1）**：单测全绿（`test_visionhub_bridge.py` 12 项）；端到端冒烟 PASS（命令桥转发
`request_video_text`/跳过非视频命令、替身桩推理、结果桥译回入库、`CommandTracker` 经 `correlation_id`
关联「已发→收到结果」、问答查到新结果且 event_id 在 tracker/库/证据三处一致）；live 多进程
（CLI→双向桥→替身→ingest→问答）打通，回流事件 `source=video-perception-visionhub-001`、
`parent_trace_id=command_id`。
**未验证风险（step2）**：替身 + 桩推理，未对接真实 VLM/dispatcher；跨机 Kafka advertised.listeners/网络；
vision hub 真实 envelope 字段需复核。详见 [phases/P8.md](../phases/P8.md)。


### 5.6 step2 跨机落地（✅ 2026-06-20 已跨机活体验证）

接真实 wangxuan repo（`/nvme2/VLM/agents_for_vision_hub`）后定案，**不改 §5.1「ANP 原生契约 + 翻译边界」**，
仅 vision hub 侧补「收命令→dispatch→产结果」胶水，且选最不侵入的形态：

- **宿主机旁路 sidecar**（`scripts/run_video_inference_glue.py`，源码留底于 ANP 仓库
  `backend/scripts/run_video_inference_glue.py`）：独立进程消费 `visionhub.world_model.info.v1`，调
  vision hub 已暴露的 `POST /api/v1/world-model/demo-dispatch` 做**真实多智能体推理**，轮询 `final_answer`，
  直接产 `edge.observation.result.v1`（`trace.correlation_id=command_id`、payload 带 camera/road）。
  **vision hub 容器零改、零重启**；删脚本即复原（wangxuan 非 git，备份在 `.anp_p8_backup/`）。
- **为何绕开其 `canonical_observation_adapter`**：它从 agent-run outbox 产结果，`trace.correlation_id=run_id`
  （与命令无关）、camera/intersection 兜底 `unknown` → 命令↔结果关联键与路段元数据会丢。sidecar 直接产，
  字段来自命令，关联键正确。
- **跨机 Kafka = 反向 SSH 隧道 + 单 broker**：仅 ANP 侧（sjx）跑 broker；`ssh -N -R 9092:127.0.0.1:9092 wangxuan`
  把 wangxuan 的 `localhost:9092` 透到 sjx broker（advertised=localhost 经隧道天然自洽）。
- **活体验证 PASS**：`request_video_text`(人民路) → sidecar 真实推理 succeeded → 回流入库
  `source=video-perception-visionhub-001`、`parent_trace_id==command_id`、问答命中；视频组容器未受影响。

§5.5 列的「未验证风险（step2）」至此**已消解**（替身→真身、桩→真实推理、跨机 Kafka 已通、envelope 字段已复核）。

**task3 真机复跑确认（2026-06-22）**：真身 `app/*.py` 全 96 文件与本地副本 byte-identical（**零漂移**），`demo-dispatch` 契约未变；单命令 + **多 hub 编排**（网关 `/tasks` 扇出 2 定向命令 → 真身真实推理 → 按 `parent_trace_id==command_id` 逐命令归因 → DeepSeek 聚合）真机验证通过，闭 [P9.md](../phases/P9.md) 的 R1。期间修了 `p8_demo.sh` 两处生命周期 bug（keeper 幂等防多实例抢消费组 + bridge/ingest pid 记到 python 真身防 `down` 杀错残留），见 [phases/P8.md](../phases/P8.md) task3 小节。

### 5.7 命令模块族 + 多 hub 协作编排（P9）

P8 是**单条**命令的双向桥；P9 在其上做**薄编排**，把视频前端能力翻转为「ANP 扇出定向命令 → 多 vision hub 执行 →
文本回流聚合」。编排不在 adapter 里——adapter 仍只做翻译；编排在 `backend/anp/video/orchestrator.py`，adapter 的
命令桥/结果桥**零改复用**。要点：

- **命令模块族**（`video/command_modules.py`）：声明可下发命令模块。MVP 仅 `request_video_text` 落地执行端（复用
  本节命令桥）；`video.detect`/`video.stream.attach`/`video.model.select` 是 vision hub 职责，**占位声明、不实现执行端**
  （ANP 不做检测/拉流/CV 模型管理），前端诚实标「外部系统(vision hub)」。
- **扇出 = N 条定向命令**：编排器对每个目标 hub 逐条发 `request_video_text` 到 `anp.video.command.v1`（各带唯一
  `command_id` + `target_agent_id`，禁 broadcast），命令桥**逐条**译给 vision hub。多 hub 各产各的结果，按
  `parent_trace_id==command_id` **逐命令归因**回流（结果桥已写入此关联键，P9 在文本库加 `parent_trace_id` 可检索列）。
- **真·多机多 hub**（R1）：依赖 wangxuan 在线（当前 DOWN）。MVP 用本地替身桩当 ≥1 hub 验证形态；真身多 hub 留待恢复。

详见 [video.md](video.md) §11、[tasks/task1](../tasks/task1)、[phases/P9.md](../phases/P9.md)。

### 5.8 摄像头/路口目录同步（对齐 wangxuan，轻数据 step1，2026-06-22）

把 ANP 的位置选择器从「task2 只从本地文本事件派生」升级为**与 wangxuan visionhub 1:1 对齐**——用户决策「逐渐对齐 wangxuan、先完整传一遍轻数据」的第一步。**只传摄像头/路口目录**（events/轨迹不传）。

- **轻数据边界**：只读真身 `cameras` 表的**轻字段**（`source_id`/`camera_id`/`name`/`district`/`intersection_name`/`primary_road`/`secondary_road`/`camera_position`/`status`/经纬度）——**绝不含视频/帧/轨迹/检测框**。真身重数据（trajectories 1.09 亿、objects 32 万）留在 wangxuan。
- **机制**（`adapters/visionhub/catalog.py` + `scripts/sync_visionhub_cameras.py`）：`ssh wangxuan` 跑 psql（`json_agg` 拉 cameras 轻字段）→ `map_camera` 映射成 ANP 原生记录 → `SqliteVideoTextStore.replace_cameras()` **全量替换**写入 ANP `video_cameras` 表。**不直连 PG、不引 psycopg、不开新隧道**（psql 在 wangxuan 本机连其 localhost PG）；wangxuan PG DSN 存 `backend/.env`（gitignored，不入库）。懂真身 schema 的耦合只在本 adapter 内。
- **键映射**：`source_id`（整数，201 唯一）= 真身稳定键（`camera_id` 是带时间戳的脏串，仅作标签）；真身无干净路口键 → `intersection_id` 由 `intersection_name` 派生 `vh-<md5前10>`（确定性 ascii，与 ANP 自有 `gg-xiongchu-minzu` 区分）。
- **`/locations` 升级**：目录非空时**优先**从 `catalog_locations()` 出（富化 intersection_name/district/camera_position/source_id），再并入文本库派生但目录未覆盖的位置（ANP 自有事件位置不丢）；目录为空则回退纯文本派生（兼容 task2）。
- **现状**：实测同步 wangxuan **201 source / 7 真实路口（天津）** + 45 无路口孤儿源；`/locations` 与 wangxuan 一一对应。注意真身 201「摄像头」实为**带时间戳的视频源/片段**（非 201 个物理摄像头）。

#### step2：回流事件挂到目录相机（2026-06-22）

step1 后目录相机 `event_count` 恒 0——非通路问题：命令→事件整链已忠实透传 `camera_id`/`intersection_id`/`road_name`（命令桥 → 真身胶水 `_build_result`/替身 `stub_visionhub_agent` 原样回显 → 结果桥落回 ANP 事件；前端 picker 选目录相机时已带真身标识）。缺口在 `store.catalog_locations()` 计数口径：路口级旧按「各相机 `camera_id` 命中之和」算，漏掉带真 `intersection_id` 但 `camera_id` 非目录值的事件。

- **对齐（不改 contracts）**：相机级 `event_count` 按 `camera_id` 精确命中（目录 201 唯一）；路口级按 `intersection_id` 归属（事件自带 `intersection_id` 命中目录，或经其相机 `camera_id` 回溯，每事件计一次）。
- **发对齐命令**：`run_video_command.py --source-id N`（`store.get_camera(source_id)` 解析真身 `camera_id`/`intersection_id`/`road_name`）；前端建任务走 picker 已自动带真身标识。
- **未把 `source_id` 入 wire 契约**：`camera_id` 唯一且整链透传，已等价按 source 归属，避免改 `contracts/`/gen_schemas。详见 [video.md](video.md) §12.5。

#### step3：历史事件回填（2026-06-22）

step1/step2 只搬目录 + 打通连接，ANP 库本身仍空（薄黑板靠按需推理喂事件）。step3 把真身 `events` 表历史事件作轻数据同步进来。

- **机制**（镜像目录同步）：`catalog.py` 加 `fetch_visionhub_events`/`map_event`（经 `store.camera_source_index()` 按 source_id 对齐相机/路口，`event_type`→category）+ 脚本 `sync_visionhub_events.py`（`--limit`/`--event-type`/`--dry-run`）。
- **轻数据边界**：只搬 `description`/`event_type`/`severity`/`detected_at`/`confidence`/`source_id`/`track_ids`——**不含 bbox/帧/轨迹像素**。
- **直写回填**（非经 Kafka，live 回流仍走 Kafka）；幂等 `message_id=vh-evt-<id>`；身份 `video-perception-visionhub-events-001`。
- **实测**：3549 条（3227 超速+322 拥堵）全对齐 7 天津路口，`new=3549 dup=0 bad=0`；live `/locations` 即见真实事件数（无需重启网关）。详见 [video.md](video.md) §12.6。

详见 [video.md](video.md) §12、`backend/scripts/sync_visionhub_{cameras,events}.py`。
