# 网关 API：前端 ↔ 网关 HTTP 契约

网关（`backend/anp/gateway`，FastAPI）是纯读模型 + 命令入口。**对外契约刻意保持与老仓库前端兼容**，使前端可照搬，只改 API base 指向新网关。前端期望结构见老仓库 `agent_network_frontend/src/types.ts` 与 `src/api/agentNetworkClient.ts`。

通用约定：
- 路径前缀 `/api/agent-network`。响应 `Content-Type: application/json`（前端据此判断是否回落 mock）。
- JSON 字段用 **snake_case**；前端 normalizer 同时接受 snake/camel。
- 网关不可用、非 JSON、或无有效节点时，前端自动回落 mock（状态条显示 `source mock`；可用时显示 `source gateway`）。
- 鉴权（可选，默认关）：`AGENT_NETWORK_REQUIRE_AUTH=true` 时读接口需 read token、命令需 admin token，走 `Authorization: Bearer <token>`。命令尝试可写审计账本（不存 token、不存完整 payload）。

## 1. GET /api/agent-network/snapshot

查询参数：`scope`（可选）。返回 `NetworkSnapshot`：

```jsonc
{
  "version": "gateway",
  "generated_at": "...Z",
  "topology_version": "traffic-v1",
  "region": "traffic",
  "summary": { "agents": 3, "relations": 6, "resources": 5,
               "healthy_percent": 92, "kafka_lag_ms": 0, "update_rate": 0.1 },
  "nodes": [ /* §1.1 */ ],
  "edges": [ /* §1.2 */ ],
  "resources": [ /* §1.3 */ ],
  "trend": [ { "t": 0, "value": 180 } ],
  "events": [ { "id": "...", "severity": "info", "title": "...", "target_id": "...", "time": "...Z" } ]
}
```

### 1.1 nodes
两类来源合并：
- **路口节点**（来自 World Status）：`node_type="region"`，`group="traffic"`，metrics 由 World Status 映射：
  ```jsonc
  { "id": "gg-xiongchu-minzu", "label": "雄楚-民族", "node_type": "region", "group": "traffic",
    "position": { "x": 0, "y": 0 }, "status": "online", "health": 80, "tags": ["traffic"],
    "metrics": { "flow": 180, "speedKmh": 29.9, "delaySec": 41.2, "queueM": 35.0, "state": "拥堵" } }
  ```
  - `status`：World Status 新鲜度 + 拥堵度映射（畅通/缓行→online，拥堵→warning，严重或超时→offline，刚建立→syncing）。`health = clamp(100 − congestion_index×100)`。
- **智能体节点**（来自 registry）：`node_type="agent"`，metrics 含心跳/能力；`status` 由 heartbeat 在线/降级映射。

### 1.2 edges
路口之间的路网连接 + 智能体与实体的关系，来自静态路网拓扑配置（`deploy/` 或 `backend` 内常量）。字段：`id, source, target, label, directed, relation_type, status, metrics`。

### 1.3 resources
来源/去向物理资源：`resource_type ∈ camera|database|detector|simulator|storage|controller`，`direction ∈ input|output|bidirectional`，`anchor_agent_id` 锚定到某节点。v1 用少量静态资源（检测器=input、控制器/库=output）。

## 2. GET /api/agent-network/projection

查询参数：`kind ∈ world_model|node|edge|resource`、`id`。返回 `InspectorProjection`：

```jsonc
{
  "target": { "kind": "node", "id": "gg-xiongchu-minzu", "title": "雄楚-民族" },
  "tabs": [
    { "id": "status", "title": "当前态",
      "blocks": [ { "type": "metric_grid", "title": "路口指标",
                    "items": [ {"label":"排队(m)","value":35.0}, {"label":"流量(veh/h)","value":180} ] } ] },
    { "id": "control", "title": "命令闭环",
      "blocks": [ { "type": "event_list", "title": "最近命令/ack", "items": [ /* ... */ ] } ] }
  ]
}
```

- block `type ∈ metric_grid|kv_list|event_list|timeseries|json`（前端已支持）。
- 路口节点：tabs 给 World Status 当前态、最近窗口、相关命令/ack。
- 智能体节点：tabs 给 registry 信息、心跳、能力、可下发命令清单（驱动 Inspector 命令面板）。
- `target` 缺失或 `tabs` 非数组时前端判为无效并回落，必须保证结构完整。

## 3. POST /api/agent-network/commands

请求体：

```jsonc
{ "target_agent_id": "traffic-virtual-001", "command_type": "set_signal_plan",
  "payload": { "desired_phase": "north_south_green", "duration_s": 25 },
  "site_id": "...", "region_id": "...", "object_id": "...", "expires_in_sec": 30 }
```

网关校验：
1. `target_agent_id` 必填且在白名单 → 否则 `400`（缺失）/ `403`（不在白名单）。
2. `command_type` 合法 → 否则 `400`。
3. **拒绝** `broadcast` 与 `agent_ids` 字段 → `400`（前端也会本地拦截）。
4. 构造 envelope：`target.agent_id = target_agent_id`，`time.expires_at = now + expires_in_sec`，发布到 `anp.<domain>.command.v1`。
5. 可写命令审计账本。

成功 `200`：

```jsonc
{ "ok": true, "command_id": "...", "topic": "anp.traffic.command.v1",
  "target": { "agent_id": "traffic-virtual-001" }, "status": "published", "message_id": "..." }
```

错误：`400` 入参非法、`403` 未授权/不在白名单、`503` Kafka 不可用、`500` 发布失败。错误体统一：

```jsonc
{ "ok": false, "error": { "code": "...", "message": "..." } }
```

网关**不伪造 ack**。命令是否被执行端接受，由前端订阅/查询 ack 反映（v1 可在 projection 的命令闭环 tab 展示最近 ack）。

## 3a. POST /api/agent-network/registrations

操作台自定义接入智能体。它服务于“外部系统暂时不能嵌 `WorldClient`”的过渡层：请求体描述一个或多个 agent 的世界注册声明，网关按同一套 world lifecycle/heartbeat 语义写入。

请求体：

```jsonc
{
  "source": "signalvision",
  "target_model_id": "traffic-control",
  "agents": [
    {
      "agent_id": "traffic-perception-sv-001",
      "agent_type": "signalvision",
      "capabilities": ["perception"],
      "command_types": [],
      "produces": [{ "topic": "anp.traffic.perception.observation.v1", "keys": ["gg-xiongchu-minzu"] }],
      "consumes": [],
      "weight": 0.92,
      "status": "online"
    }
  ]
}
```

语义：
- 需要 admin token（鉴权开启时），与 `/commands` 同级写权限。
- `agent_id`、`agent_type`、`agents[]` 必填；`produces/consumes` 是 `Channel{topic, keys[]}`。
- 网关有 Kafka producer 时，先向 `anp.world.agent.lifecycle.v1` 发布 `AGENT_REGISTERED`，再向 `anp.world.agent.heartbeat.v1` 发布 `AGENT_HEARTBEAT`，partition key 仍是 `agent_id`；随后刷新本地 registry。
- 无 producer 的开发态仍刷新本地 registry，但响应 `persistence="registry_only"`，重启不保证保留。
- `target_model_id` 是操作台上下文，不直接伪造成员关系；模型归属仍由 `/world` 根据 model topic 边界和 agent topic/key 声明推导。

成功 `200`：

```jsonc
{ "ok": true, "source": "signalvision", "target_model_id": "traffic-control",
  "registered": ["traffic-perception-sv-001"], "persistence": "world_topics", "world": { /* /world 当前视图 */ } }
```

错误：`400 invalid_body|invalid_registration`，`401 unauthorized`，`500 registration_publish_failed`。

## 4. POST /api/agent-network/edge-inference（本期可选）

前端 `runEdgeInference` 会调用。v1 仅对声明 inference 能力的 agent 生效；交通虚拟体不支持时返回结构化错误，前端按钮显示失败、不崩主界面：

```jsonc
{ "ok": false, "agent_id": "...", "mode": "auto",
  "error": { "code": "unsupported", "message": "edge inference 未在本期交通域启用" } }
```

## 5. GET /api/agent-network/timeseries/{health|latest|summary|events}（冷路径，本期未启用）

冷路径 TimescaleDB 本期不做，但**保留接口**，返回结构化「未启用」，让前端 Inspector 时序区显示空态/错误态而不影响主界面：

```jsonc
{ "ok": false, "error": { "code": "timeseries_disabled",
  "message": "cold path not enabled in v1" } }
```

前端 `fetchTimeseriesJson` 把 `ok:false` 当错误结果处理，主 snapshot/projection 不受影响。

## 5a. GET /api/agent-network/sv-network 与 /sv-maps（SignalVision 只读 relay）

这两个接口是 SignalVision 接入的**务实例外**：网关只读 relay SV Dashboard，不把结果写入 Kafka 黑板，
用于前端镜像 SV 当前地图与 JunctionAgent 列表。

- `GET /sv-network`：relay SV `/api/network` + `/api/junctions/summary`，返回紧凑几何：
  ```jsonc
  { "ok": true, "source": "signalvision",
    "junctions": [
      { "id": "1", "x": 0, "y": 0, "congestion": 0.42,
        "junction_type": "traffic_light", "is_active": true,
        "total_vehicles": 12, "total_halting": 3 }
    ],
    "edges": [ { "id": "...", "x1": 0, "y1": 0, "x2": 1, "y2": 1, "lanes": 2, "length": 80.0 } ],
    "bounds": { "minX": 0, "maxX": 1, "minY": 0, "maxY": 1 },
    "junction_count": 9 }
  ```
  前端交通地图、智能体列表、实时交通数据、路口指标共用这份当前 SV 路网；因此切换地图后这些面板随
  `/sv-network` 的 junction/edge 集合一起变化，不再使用 `snapshot` 中的静态 `gateway/topology.py` 路口冒充 SV 智能体。
- `GET /sv-maps`：relay SV `/api/maps`，返回 `{ok:true,maps:[{name,path,size}],count}`，供前端切图下拉。

SV 不可达时返回 `503 {ok:false,error:{code:"sv_unreachable",...}}`；前端地图可回落静态底图，SV 工具面板显示未连接。

## 5b. 视频文本问答 + 协作任务（co-host，P7/P9）

视频域逻辑独立在 `backend/anp/video/`，**co-host** 到网关进程（同命名空间，前端复用 `/api/*` 反代）。网关
**不算视频聚合**（AGENTS §3.4，聚合在 `video/orchestrator.py` 任务体侧）；视频命令直发 `anp.video.command.v1`
（≠ 交通 `anp.traffic.command.v1`，故不复用 §3 的 `/commands` 管道，但守同样校验纪律）。契约/语义见
[video.md](video.md) §6/§11、命令模块族见 [adapters.md](adapters.md) §5.7。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent-network/video-text/events` | 入库一条视频文本事件（P7） |
| POST | `/api/agent-network/video-text/query` | 检索 + 问答（`QueryResponse`，P7） |
| GET | `/api/agent-network/video-text/health` | 库内条数 + LLM 是否启用 |
| POST | `/api/agent-network/video-text/tasks` | 新建协作视频任务 → 编排器**扇出 N 条定向命令**（每条带 `target_agent_id`，禁 broadcast）→ 返回 `VideoTask`（含各 `command_id`）。占位模块 → 400；无 producer → 503（P9） |
| GET | `/api/agent-network/video-text/tasks` | 任务列表（批量本地刷新回流状态；全部回流时缓存本地规则摘要，不出网，P9） |
| GET | `/api/agent-network/video-text/tasks/{id}` | 任务详情（按 `command_id` 归因 + 聚合答案 + 证据；默认本地规则摘要快路径，`?llm=true` 可重新调用 LLM 精炼；未知 → 404，P9） |
| GET | `/api/agent-network/video-text/command-modules` | 命令模块声明枚举（区分可下发/占位，P9） |

## 6. 部署形态（P4 落地）

- 开发：前端 `cd frontend && npm run dev`（18180）。默认**不设** `VITE_AGENT_NETWORK_API_BASE`，由 vite dev/preview 把 `/api/*` 反代到 `VITE_GATEWAY_PROXY`（默认 `http://127.0.0.1:8000`，即 `run_gateway.py`）；设了绝对 base 则客户端直连、不反代（生产/前端与网关不同源）。
- 取数开关：前端沿用老约定，仅当 URL 带 `?source=gateway` 才尝试网关 snapshot（否则纯 mock 简化壳）。网关有有效节点时状态条显示 `source gateway`，并在右侧 Inspector 挂回命令面板 + 命令闭环（projection 3s 轮询拾取 ack）。
- 一体化：网关同时托管前端 `dist` 并反代 `/api/*`（沿用老仓库 `serve-with-gateway` 思路，本期暂未做静态托管）。
- 前端不直连 Kafka；所有数据经本网关。前端字段对齐：命令选项以 snapshot agent 节点的 `metrics.commandTypes` 为权威来源（见 §1.1）。
- 交通 SV 工具面板的**当前地图事实源**是 `/sv-network`。`snapshot` 仍是 ANP 读模型（World Status + registry + 静态拓扑），
  不再用于“智能体列表”里的 SV JunctionAgent 枚举。

## 7. 实现说明（v1，P3 落地）

实现见 `backend/anp/gateway/`，registry 见 `backend/anp/registry/`。要点：

- **网关自持读模型**：网关进程独立于系统级智能体，后台用消费线程订阅
  `status.intersection`（→ 自己的 World Status 当前态 + `trend`）、`ack`（→ 命令日志回填）、
  `agent.lifecycle`/`agent.heartbeat`（→ registry 在线状态）。HTTP 层只读这份内存态，不回放、不聚合。
- **节点来源**：路口节点恒来自静态拓扑（`gateway/topology.py`，v1 雄楚大道三路口），叠加 World Status
  指标；无 World Status 时状态 `syncing`、指标置 0（保证 snapshot 节点非空，前端不因空节点回落）。
  智能体节点来自 registry；另有一个网关自身的 `service` 节点（`traffic-gateway-001`）供资源锚定。
- **路口节点 metrics 键**：`flow / speedKmh / delaySec / queueM / state / congestionIndex`（world-status.md §5 映射）。
- **summary**：`agents` 只计智能体节点；`healthy_percent` 为全部节点 health 均值；`kafka_lag_ms=0`（v1 不测量）；
  `update_rate = 1/WINDOW_SIZE_SEC = 0.1`（World Status 产出频率 Hz）。
- **命令错误码**（统一错误体 `{ok:false,error:{code,message}}`）：
  - `400`：`missing_target_agent_id`、`broadcast_not_allowed`（带 `broadcast`/`agent_ids`）、
    `invalid_command_type`、`invalid_expires_in_sec`、`invalid_payload`、`invalid_body`。
  - `403`：`target_not_whitelisted`（目标不在 registry）、`command_not_allowed_for_target`（目标不接收该命令类型）。
  - `503`：`kafka_unavailable`；`500`：`publish_failed`。
- **白名单**：由 registry 裁决（谁注册了、接收哪些 `command_types`）。v1 种子注册 `traffic-virtual-001`
  （perception+exec，接 `set_signal_plan`）与 `traffic-system-001`（不接命令）。
- **网关只校验命令外形 + 白名单**，不做业务安全判定（相位/时长范围属执行端 Safety Guard，protocol.md §7）。
- **projection 始终返回结构完整**（`target` + `tabs`）：未知 id 也回最小合法投影，避免前端整体回落 mock。
- **鉴权**：`AGENT_NETWORK_REQUIRE_AUTH=true` 时读接口需 `AGENT_NETWORK_READ_TOKEN`（admin token 也可读）、
  命令需 `AGENT_NETWORK_ADMIN_TOKEN`，走 `Authorization: Bearer`。默认关闭。
- **运行**：`python backend/scripts/run_gateway.py [--port 8000] [--no-consumers]`（开发期 CORS 全开）。
