# 统一世界平台 —— 接入说明书（交通组 / 视频组）

> 本文 = **平台说明** + **两组的接下来任务**。配套：契约见 [protocol.md](protocol.md)/[naming.md](naming.md)，
> 交通 adapter 现状见 [adapters.md](adapters.md)，视频域见 [video.md](video.md)。
> 平台地基首刀已实施并验收（契约/registry/`backend/anp/world/`），本文是给你们接入用的。

## 0. 一句话

平台是一个**由 Kafka 维护的「统一世界」**：每个智能体是带「通道」的世界公民、自己往世界级名册报到；
每个 **model** 是一条预编排好的工作流，治一个城市问题、管辖一个 agent 子集（model 本身也是 agent）。
**接入方只用 `WorldClient` 自注册 + 写一个 `ModelSpec`，不要再各搭一套并行栈**——名册可见性、命令/ack/心跳、
网关世界视图都免费拿到。

---

## 1. 平台总览（你们需要知道的模型）

- **world = 所有 topic 的并集**；没有唯一的「World Status」，每个 model 按需从相关 topic 投影出自己的视图。
- **agent = 世界公民**，记录 = `{id, type, capabilities, command_types, 通道(produces/consumes), weight}`。
  加一个 agent = 它启动时自注册一条 lifecycle。
- **model = 预编排工作流**，管辖一个 agent 子集；model 也自注册成一个 agent（`agent_type="model"`）。
- **发现 = 纯 Kafka 自描述**：所有 agent 往**世界级** topic 报到——
  - `anp.world.agent.lifecycle.v1`（**compacted 名册**，key=agent_id，每 agent 留最新一条）
  - `anp.world.agent.heartbeat.v1`（活性流）

  registry 用 **earliest** 读这两条重建世界视图。**当前是「双读过渡」**：registry 同时订 world + 交通两套
  lifecycle，所以**你们迁移前的老 agent 不会从名册消失**，迁完再撤交通订阅。
- **topic 命名** `anp.<domain>.<layer>.<name>.v<major>`（详见 [naming.md](naming.md)）。topic 按「域·层·种类」分，
  **实体规模交给 partition / key，绝不一个实体一个 topic**。
- **通道 `Channel{topic, keys[]}`**：`keys` 是实体 id（如 `intersection_1_1`/`camera-001`），空=整条 topic 不分实体。
  Kafka 订阅是 **topic 级**，key 只进**声明 / catalog / 寻址**（客户端按 key 过滤是后续优化）。
- **命令**必带 `target_agent_id`、禁止 broadcast；Safety Guard 在**执行端**；ack 带 `command_id` 回指。
- **一个 model = 一个 consumer group**；同一 agent 可被多 model 共享（跨 group 各读各的）。

---

## 2. 接入接口（照着用）

### 2.1 `WorldClient` —— 自注册 + 心跳 + 收发（`backend/anp/world/client.py`）

这是你们**必须**采用的那一层：让 agent 出现在统一世界名册里。

```python
import threading
from anp.contracts import Channel, TrafficTopics, observation_envelope
from anp.world import WorldClient

wc = WorldClient(
    "traffic-perception-sv-001",      # agent_id（全局唯一）
    agent_type="signalvision",        # 自由字符串
    capabilities=["perception"],      # 能力标签
    command_types=[],                 # 可接收的命令类型（执行体才填）
    produces=[Channel(topic=TrafficTopics.OBSERVATION, keys=["intersection_1_1"])],
    consumes=[],                      # 订阅的通道（执行体填 command）
    weight=1.0,                       # 先填 1.0，平台暂不用
    bootstrap=None,                   # None=ANP_BOOTSTRAP/localhost:9092
)

wc.register()                         # → anp.world.agent.lifecycle.v1（key=agent_id，compacted）
stop = threading.Event()
wc.start_heartbeat(5.0, stop)         # 后台线程周期心跳

# 发数据（用现成的契约 builder 造 envelope，WorldClient 只负责 publish）
wc.publish(TrafficTopics.OBSERVATION, observation_envelope(agent_id=wc.agent_id, payload=obs, event_ts=ts))

# 退出
stop.set(); wc.deregister(); wc.close()
```

公共 API：`register()` / `deregister()` / `heartbeat(status, last_error)` / `start_heartbeat(interval, stop)` /
`publish(topic, env)` / `subscribe(topics, group_id=..., auto_offset_reset="latest")` / `close()`。

**注册流程（端到端）** —— 注册 = 往世界级 lifecycle topic 发一条「自我声明」，**没有中心服务可调**：

1. **发起方（agent 进程）**：`register()` 把 `AgentLifecyclePayload`（id / type / capabilities /
   command_types / 通道 produces·consumes / weight，model 还带 `members`）装进 envelope
   （`event_type=AGENT_REGISTERED`，`source.agent_id=agent_id`），发到 `anp.world.agent.lifecycle.v1`，
   **key=agent_id**（compacted，每 agent 只留最新一条）。`start_heartbeat()` 后台周期发
   `AGENT_HEARTBEAT` 到 `anp.world.agent.heartbeat.v1`；退出 `deregister()` 发 `AGENT_DEREGISTERED`。
2. **接收方（registry，跑在网关里，自动）**：**lifecycle consumer 从 earliest** 读 → `apply_envelope`
   → `register()` 建/更新一条 `AgentRecord`（世界名册）；**heartbeat consumer 从 latest** 读 →
   刷新 `last_heartbeat_ts` → 按新鲜度派生 `online/degraded/offline`；网关 snapshot / `/world` 读
   `registry.all()` 渲染节点（地图摆位 = 通道 key 对应实体的坐标）。
3. **要点**：register（一次性·compacted·名册）vs heartbeat（周期·活性）是**两条 topic、两种语义**，
   别混；register **幂等**（能力/通道变了重发一条即可）；**自描述、无中心**——任何居民（网关、别的
   model）从 earliest 读 compacted lifecycle 就能重建「世界有谁、各自什么通道」，**新加一个 agent 平台零改**。

最小写法见上面的代码片段：给它 id + 通道，`register()` + `start_heartbeat()` 即进世界。

### 2.2 数据 / 命令 / ack 契约（已有，别另造）

- **造 envelope** 一律走 `anp.contracts` 的 builder：`observation_envelope` / `video_text_envelope` /
  `status_envelope` / `command_envelope` / `ack_envelope` / 通用 `make_envelope`。**不要手搓 dict**。
- **执行体**（接命令）：订 `<domain>.command.v1` → 目标匹配(`target.agent_id`) → 去重 → 过期(`expires_at`) →
  本地 Safety Guard → 执行 → 回 `ack_envelope`。可参考 `agents/virtual_traffic.py::VirtualTrafficExecutor`
  与 `adapters/signalvision/executor.py`。
- **命令幂等**：去重表可由重放本体既往 ack 重建（见虚拟体 `rebuild_dedup_from_acks`）。

### 2.3 `ModelSpec` + `ModelRuntime` —— 声明一个 model（`backend/anp/world/{spec,runtime}.py`）

一个 model = 一份 JSON 规格 + 一个 workflow 对象（纯逻辑，约定 `feed_record(value)`、可选 `flush()`）。

`specs/your_model.json`：
```json
{
  "model_id": "traffic-control",
  "problem": "路口信号管控：聚合感知观测 → 产出路口 World Status",
  "member_agent_ids": ["traffic-virtual-001"],
  "subscribe_topics": ["anp.traffic.perception.observation.v1"],
  "produce_topics": ["anp.traffic.status.intersection.v1"],
  "workflow": "system_agent",
  "weight": 1.0
}
```
- `subscribe_topics` 留空时，ModelRuntime 会用 registry 按成员 agent 的 `produces` 求并集推导。
- 跑：`python backend/scripts/run_model.py --spec specs/your_model.json`。model 启动时**走同一套注册
  流程自注册成一个 agent**（`agent_type="model"`，并把 `member_agent_ids` 作为 `members` 自报进世界），
  一 model 一 consumer group 跑 workflow。所以它一启动就出现在世界名册 + 左侧「自发现 model 列表」，
  并带上「它管辖谁」——前端据此高亮成员、画治理边。
- **自定义 workflow**：实现一个带 `feed_record(value)`（+可选 `flush()`）的对象，在 `run_model.py::_build_workflow`
  里注册名字，或直接用 `ModelRuntime(spec, workflow=你的对象, bootstrap=...)`。

### 2.4 发现（registry catalog）

registry 已提供（`backend/anp/registry/registry.py`）：`catalog_by_topic()`（按 topic/per-key 反查谁产谁消）、
`agents_covering(topic, key=None)`、`agents_with_capability(cap)`。

网关对外的只读接口 `GET /api/agent-network/world` 暴露统一世界给前端：
- `agents[]`：id / type / 状态 / 通道 / **位置**（由通道 key→实体坐标派生，无坐标=非地理公民）/ `governed_by[]`（归属哪些 model）；
- `models[]`：model_id / problem / `members[]`（自报的管辖成员）/ produce_topics / 状态；
- `catalog`：topic→producers/consumers（含 per-key）。

实体坐标来源：路口取自拓扑；**其它实体（摄像头等）留一个 location provider 钩子，未来由视频组提供**，
查不到坐标的 agent 自动落入「非地理公民」。

---

## 3. 交通组的接下来任务

1. **把 SV 感知 adapter / 信号执行体迁到 `WorldClient`**：✅ 当前已落地**过渡双注册层**。
   `run_signalvision_adapter.py` / `run_signalvision_exec.py` 仍保留老 `anp.traffic.agent.*` lifecycle/heartbeat，
   同时通过 `backend/anp/adapters/signalvision/world.py` 构造 `WorldClient` 向 `anp.world.agent.*` 报到。
   感知体声明 `produces=[Channel(anp.traffic.perception.observation.v1, keys=[intersection_id...])]`；执行体声明
   `consumes=[Channel(anp.traffic.command.v1, keys=[intersection_id...])]`、`produces=[Channel(anp.traffic.ack.v1)]`，
   因而已能进入 `/api/agent-network/world` 的 per-key catalog。2026-06-27 已在服务器活体验证：前端 dev server
   通过 gateway proxy 能看到 `traffic-perception-sv-001` / `traffic-exec-sv-001` 在线，ANP `/commands` 可下发
   `control_signal_inference{start,maxpressure}` 让 SV status 进入 `running=true`（无 SUMO 环境时走 SV 演示回放兜底）。
   过渡期双读仍保留，待所有交通/视频 agent 都迁完后再撤老 traffic lifecycle 订阅。
2. **把交通聚合正式跑成 model**：样板已给（`specs/traffic_system.json` + `run_model.py`，workflow=`system_agent`，
   `SystemAgent` 类零改）。把线上从 `run_system_agent.py` 切到 `run_model.py`（注意：**别同时起两者**，否则同一观测
   被两个 group 各产一份 status，输出翻倍）。
3. **SV 形态 a：per-junction 真控信号闭环（要重构 SV）**。背景：SV `/update` 只写展示层不驱动 SUMO，HTTP 面只有
   全局起停算法——没有单路口真控接口（详见 [adapters.md](adapters.md)）。已定位的最小凿口（SV 在 `~/project/SignalVision`，
   非 git，改前留 `*.anp-bak-*`）：
   - **per-junction 覆盖**：`adapter/libsignal_adapter.py` 的 `signal_update` 写灯循环（约 :434），算法算出
     `all_actions` 后、`pseudo_step` 写灯前，按 junction_id 查 `external_overrides: {junction_id:(phase_index,expires_at)}`，
     未过期用命令相位、过期回落算法（固定优先级=外部>算法+超时回退）。`pseudo_step` 自带黄灯/最小绿保护。
   - **状态上行**：`dashboard/integration/dashboard_controller.py` 的 `_update_junction_agents` 末尾（约 :321），
     复用 `get_all_summaries()`/`get_state_dict()` 发 Kafka，与现有 `/api/junctions/<id>` 同源。
   - **铁律**：SUMO 后端是 **libsumo（进程内、非线程安全）**——所有 traci 写必须留在仿真 daemon 线程；Kafka
     consumer 只写 override 字典、producer 异步入队后台发，绝不在 Kafka 线程碰仿真对象。`traffic` env 需
     `pip install confluent-kafka`。
   - **命令契约**：per-junction 信号命令传 `junction + phase_index + 时长/过期`（SV 认相位索引，非任意红黄绿）；
     命令在算法决策节拍（`action_interval`，默认 10 步）生效、非瞬时。
4. **全部迁完后**：通知平台侧撤掉 registry 的交通 lifecycle 订阅、退役 `anp.traffic.agent.lifecycle/heartbeat.v1`。

## 4. 视频组的接下来任务

1. **第一步、最有价值**：**让视频感知体自注册**。现状是 `video/` + visionhub 桥**完全不自注册**、对统一名册隐形
   （grep `lifecycle/register/heartbeat` 为空）。用 `WorldClient` 给摄像头 / vision-hub 桥报到，声明通道
   `produces=[Channel("anp.video.perception.text.v1", keys=[camera_id 或 road])]` + 周期心跳。改完它们立刻进世界名册、
   网关 snapshot 可见。
2. **数据管线保留**：`video/` 的 ingest/store/qa/retrieval 是视频域**内部实现**，不必动；要换的只是
   **注册/发现这层**——从「并行栈」改成平台原语。
3. **（可选）把视频域 model 化**：视频问答更像「服务」而非流式聚合，不完全贴 `ModelRuntime` 的
   `feed_record→produce` 模式。最小先只做「agent 自注册可见」即可；若要 model 化（例如「视频事件感知」model 管辖
   一组摄像头、产出文本事件），按 §2.3 写 ModelSpec + 自定义 workflow / 自写 runner。
4. **下行命令**：视频推理命令链路（`anp.video.command.v1` → vision hub）已存在（见 [video.md](video.md) §10）；
   迁移时让 vision hub 桥也作为执行体在世界里登记 `command_types`，命令寻址才进 registry 白名单。

## 5. 过渡保证 & 注意事项

- **双读过渡**：registry 同时订 world + 交通 lifecycle/heartbeat，**未迁移的 agent 仍可见**，各自按节奏迁。
- **通道 keys 必须与实际发数据用的 `scope.object_id` / `intersection_id` / `camera_id` 一致**，否则 catalog 对不上。
- **lifecycle 按 agent_id 做 Kafka key**（compaction 前提）；`WorldClient` 已保证（走 `partition_key`=source.agent_id）。
- **registry 用 earliest 重建名册**：心跳新鲜度按消息 `event_ts`（emission）判定，重放历史心跳不会把死 agent 误判成在线。
- **compaction**：world lifecycle topic 已配 `cleanup.policy=compact`（`deploy/topics/topics.txt`）；建 topic 走
  `deploy/create_topics.sh`（第 4 列可写 config）。
