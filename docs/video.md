# 视频域设计：视频文本事件问答（P7）

本文是「视频监控 World Model（`video` 域）」一阶段的权威设计。范围与边界见 [AGENTS.md](../AGENTS.md) §1（P7 例外条款）与 [phases/P7.md](../phases/P7.md)。命名见 [naming.md](naming.md)，envelope/协议见 [protocol.md](protocol.md)。

## 1. 目标与边界

**做什么**：把视频组的「事件文本问答」能力以 ANP 原生方式迁入——视频智能体作为**感知体**，把视频大模型处理后的**文本事件**发到 ANP；ANP 维护集中文本事件库 + 检索 + 问答入口。用户按日期/时间/路段提问，检索命中文本后由 LLM（GLM）归纳作答、附证据。

**不做（一阶段）**：

- **原始视频不进 Kafka**——只接收处理后的文本事件（含指向片段的 `artifact_ref`，不存视频）。
- 不搬旧系统（`agents_for_vision_hub`）运行时；旧代码仅作接口形态/交互参考。
- 不做视频流播放、向量语义检索、真实视频模型上报、分布式多机文本库同步、状态层 LLM。

健康迁移：能力进 ANP，不带旧运行时包袱。

## 2. 数据流

```
[视频处理体（多机；一阶段=桩/样例回放）]
   │ 发布视频文本事件（text + 时空标签 + 实体），原始视频不进 Kafka
   ▼
anp.video.perception.text.v1   ← 感知层 topic（video 域）
   │
   ▼
[视频文本 ingest]  订阅 → 写集中文本库（VideoTextStore，SQLite）
   │                         ▲ HTTP 便捷入口 POST /events 也写同一库
   ▼
[检索问答]  按时间/路段/关键词检索 → LLM(GLM) 归纳 + 规则摘要兜底
   │  HTTP：POST /api/agent-network/video-text/query（对齐老前端 QueryResponse）
   ▼
[前端「视频事件问答」面板]
```

- Kafka 是规范上行路径（视频感知体作为感知智能体发布）；HTTP `POST /events` 是便捷/桥接入口。两者落同一库，按 `message_id` 幂等去重。
- 检索问答 HTTP co-host 在网关进程（前端复用现有 `/api/*` 反代），逻辑独立在 `backend/anp/video/`，**不混入交通域世界状态计算**（AGENTS.md §3.4）。

## 3. 契约

复用统一 envelope（[protocol.md](protocol.md) §1），新增一个 event_type / topic / payload：

| topic | event_type | payload 关键字段 |
|---|---|---|
| `anp.video.perception.text.v1` | `observation.video.text` | `camera_id`、`road_name`/`intersection_id`/`road_segment`、`text`、`summary`、`category`、`tags[]`、`entities{}`、`start_ts`/`end_ts`、`artifact_ref`、`source_model` |

**契约要点（不重复 envelope 字段，naming.md §6）**：

- `event_id → envelope.message_id`、`source_agent_id → source.agent_id`、`event_ts → time.event_ts`、`confidence → quality.confidence` 一律复用 envelope；payload 只放视频特有字段。
- payload 的 `category`（事故/拥堵/违章…）与 envelope 的 `event_type`（消息类型）刻意区分命名，避免撞名。
- `scope.object_id` 默认取路口/路段/摄像头之一，保证同实体分区稳定；感知层分区键 = `source.agent_id`（naming.md §3）。

payload JSON Schema 见 `schemas/video_text.schema.json`（由 `gen_schemas.py` 派生，勿手改）。

## 4. 集中文本事件库（VideoTextStore）

可替换接口 `append(env)` / `search(filters)` / `get(event_id)` / `count()`。一阶段实现 `SqliteVideoTextStore`（标准库 sqlite3，零依赖），表 `video_text_events`，索引 `event_ts`/`road_name`/`intersection_id`/`camera_id`。`event_ts` 存归一化 ISO8601 UTC（带 Z），字符串可直接比较做时间窗过滤。后续可换 PostgreSQL/向量库而不动上层。

> 落库说明：这是对「冷路径不落库、只留接口」既定约定的**受控破例**，仅限 video 域（P7 必须按日期/路段检索历史）。**不改变交通域冷路径仍不落库**的约定。

检索过滤（`SearchFilters`）：`time_from`/`time_to`、`road_name`、`intersection_id`、`camera_id`、`category`、`keywords[]`、`limit`。结构化条件 AND；关键词组内 OR（匹配 text/summary/category/road_name）。结果按 `event_ts` 倒序。

问题解析（`extract_filters`，规则启发式，无 LLM 也可用）：从自由文本抠路段（先剥时间短语）；**有路段/路口过滤时不自动加关键词硬过滤**（召回靠路段+时间，相关性交给 LLM，避免漏召回），仅在无空间过滤时用类别词收窄宽问题；显式 `keywords`/`category` 始终生效。

## 5. 检索问答（QA）

链路：问题 → `extract_filters` → `store.search` → 合成。合成默认接 **GLM**（RAG：把命中文本喂给 LLM 据实归纳、带引用）；无 key 或 LLM 报错回退**规则摘要**（按时间排序归纳）。响应对齐老前端 `QueryResponse`：

```json
{
  "answer": "…",
  "tool_calls": [{"tool": "search_video_text_events", "arguments": {…}, "result": {"count": N}}],
  "evidence": [{"event_id","event_ts","camera_id","road_name","intersection_id","category","summary","text","confidence","artifact_ref"}],
  "warnings": []
}
```

LLM 配置走环境变量（`backend/.env`，已被 .gitignore 排除）：`OPENAI_BASE_URL`/`OPENAI_MODEL`/`OPENAI_API_KEY`（OpenAI 兼容；默认 GLM via z.ai `glm-5.2`）。z.ai 为国际站需经代理（默认 `127.0.0.1:7897`，`ANP_LLM_PROXY` 可覆盖/置空）。**glm-5.2 为推理模型**：`reasoning_content` 与答案共用 `max_tokens`（默认 4096，给足以免 content 被吃空），答案取 `message.content`。代理仅作用于 LLM 出网调用，本地 Kafka/网关不受影响。

## 6. HTTP 接口

前缀 `/api/agent-network/video-text`（与网关同源，前端复用反代）：

```
POST /events            入库一条视频文本事件（VideoTextEventIn）→ {event_id, stored, count}
POST /query             检索 + 问答（VideoTextQueryRequest）→ QueryResponse
GET  /health            {ok, service, count, llm_enabled}
GET  /locations         位置枚举（路口→摄像头层级，从库派生）→ LocationsOut（task2，§12）
GET  /events            分页浏览库记录（过滤）→ EventBrowseOut（task2，§12）
GET  /events/{event_id} 取单条完整记录（含 envelope）→ EventRecordOut（task2，§12）
```

挂载方式：`run_gateway.py` 自动 co-host（`include_video_routes`）；或 `run_video_qa.py` 独立起 FastAPI（`create_video_app`）。

## 7. 身份（naming.md §4）

- 视频感知体：`video-perception-001`（role=perception，发文本事件）。
- 视频问答任务体：`video-task-001`（role=task，检索+合成）。

## 8. 运行与验证

```bash
# 建 topic（含 anp.video.*）
bash deploy/create_topics.sh
# 端到端冒烟（样例 → Kafka → ingest → 检索问答；GLM 探针）
python backend/scripts/smoke_video_qa.py
# live：回放样例 / 起 ingest / 起问答（或由网关 co-host）
python backend/scripts/replay_video_text.py
python backend/scripts/run_video_ingest.py --from-beginning
python backend/scripts/run_gateway.py --port 8000   # co-host 问答路由
# 前端：?source=gateway 时显示「视频事件问答」面板
```

## 9. 一阶段遗留 / 后续

- 一阶段为 fake 样例 + 单机 SQLite；真实视频智能体上报、向量语义检索、多机文本库同步留后续。
- GLM 走国际站代理，离线/无代理时回退规则摘要（已兜底）。
- 时间过滤为 UTC 字符串比较；中文口语时间（「下午」）的精确解析依赖前端传结构化时间或后续 LLM 抽取增强。

## 10. 双向交互：请求视频推理（P8）

P7 是**上行**文本（视频感知体主动发文本事件）。P8 补**下行命令**，闭合对称双向环：ANP 主动「请求 vision hub 对某摄像头/路段做一次视频推理」，vision hub 执行后**回传文本结果**（永不传视频）→ 经 P7 ingest 入库 → 问答。命令与问答**解耦**（异步黑板，非同步等待）。详细设计见 [phases/P8.md](../phases/P8.md)，翻译边界见 [adapters.md](adapters.md) §5。

### 10.1 数据流

```
run_video_command.py（CLI，命令源 video-task-001）
 └─► anp.video.command.v1 ──► [adapters/visionhub 命令桥] ──译──► visionhub.world_model.info.v1
        (request_video_text)                                          └─► vision hub 推理（step1=替身桩）
                                                                                  │ 产 observation.traffic.video_text
 P7 ingest ◄─ anp.video.perception.text.v1 ◄─ [adapters/visionhub 结果桥] ◄─译─ edge.observation.result.v1 ◄┘
   └─► 文本库 → 问答（P7 现成，零改）
   对账：CommandTracker 用 command_id（=vision hub correlation_id）记「已发→收到结果」
```

### 10.2 契约（控制层，最小新增）

| topic | event_type | payload | 说明 |
|---|---|---|---|
| `anp.video.command.v1` | `command` | 通用 `CommandPayload`，`command_type=request_video_text` | params：`camera_id`、`road_name`/`intersection_id`/`road_segment`、`time_window{time_from,time_to}`、`prompt`、`clip_ref`（只传指针不传视频） |

- 复用通用 envelope/`command_envelope`/`CommandPayload`/`VideoTextEventPayload`（P7），仅 `CommandType` 加 `request_video_text` 枚举值；命令 schema 重生成。
- 结果回流复用 P7 `anp.video.perception.text.v1` + `VideoTextEventPayload`，**ingest/库/QA 零改**。
- **关联键 = `command_id`**：命令译给 vision hub 时写入其 `trace.correlation_id`；结果回流时读回，落到 ANP 文本事件 envelope 的 `trace.parent_trace_id`，使「命令↔回流文本」可追溯。

### 10.3 翻译边界 adapters/visionhub

ANP 内部一律说 ANP 契约；`backend/anp/adapters/visionhub/` 是唯一懂 vision hub 原生 topic/envelope 的地方（镜像 SignalVision adapter）。命令桥/结果桥/对账表的职责与模块见 [adapters.md](adapters.md) §5。视频推理请求**非控制动作**，不走信号配时 Safety Guard；vision hub 侧本地有自己的限流/安全闭环（step2）。

### 10.4 运行与验证

```bash
# 端到端冒烟（step1：命令→桥→替身桩推理→桥→入库→问答；真实 Kafka，本机两程序）
python backend/scripts/smoke_video_command_loop.py
# live 多进程：起双向桥 + 替身 + ingest，再 CLI 发命令
python backend/scripts/run_visionhub_bridge.py           # 命令桥 + 结果桥（共享对账）
python backend/scripts/stub_visionhub_agent.py           # step1 vision hub 替身（桩推理）
python backend/scripts/run_video_ingest.py               # P7 入库
python backend/scripts/run_video_command.py --camera-id cam-minzu-east-001 --road-name 民族大道 --prompt "最近有没有事故或拥堵？"
```

> step1 的 vision hub 外部 topic（`visionhub.world_model.info.v1` / `edge.observation.result.v1`）不归 ANP `deploy/topics` 管；本机 broker 关了 auto-create，故脚本在**本地默认**时自动幂等 ensure（`ensure_visionhub_topics`）。step2 跨机时 vision hub 用它自己的 broker（已有这些 topic），不去远端建。

### 10.5 step1 遗留 / step2（跨机）

- step1 用替身 + 桩推理，未验证真实 VLM/真实 dispatcher 路径；问答自动触发推理的同步闭环留后续。
- ~~step2 跨机主风险是跨机 Kafka + vision hub 侧补胶水 + 真实字段复核。~~ ✅ **step2 已跨机活体验证（2026-06-20），并于 2026-06-22（task3）真机复跑确认**：真身源码零漂移（96 个 `app/*.py` byte-identical）、`demo-dispatch` 契约未变、跨机 Kafka（反向 SSH 隧道单 broker）稳定、单命令 + **多 hub 编排**（网关 `/tasks` 扇出 2 定向命令→真身真实推理→按 `parent_trace_id` 归因→DeepSeek 聚合）均通过。详见 [phases/P8.md](../phases/P8.md) task3 小节 + [phases/P9.md](../phases/P9.md)（R1 闭合）。


### 10.6 step2 跨机落地（✅ 2026-06-20 已跨机活体验证）

vision hub 真身在 wangxuan（docker 容器，`network_mode:host`，PG/Milvus/Redis 在跑）。step2 以**宿主机旁路
sidecar**（`scripts/run_video_inference_glue.py`）打通：消费 `visionhub.world_model.info.v1` → 调其
`POST /api/v1/world-model/demo-dispatch` 做**真实多智能体推理**（qwen-plus）→ 轮询 `final_answer` → 直接产
`edge.observation.result.v1`（`correlation_id=command_id`）→ ANP 结果桥译回入库问答。**vision hub 容器零改
零重启**；跨机 Kafka 走**反向 SSH 隧道 + 单 broker**。活体 PASS：命令(人民路)→真实推理→回流入库→问答命中，
关联键三处一致，视频组容器未受影响。详见 [phases/P8.md](../phases/P8.md) §step2 与 [adapters.md](adapters.md) §5.6。

## 11. 协作视频任务编排（P9）

P8 是**一条**命令的双向环。P9 把它升成**多 vision hub 协作编排** + **问答主界面**：把视频前端那四个 mock 假壳
tab（视频流接入/目标检测/事件摘要/模型管理）**翻转**为「ANP 下发命令 → vision hub 执行 → 文本回流 → 聚合」。
核心立场不变：**ANP 是轻数据黑板，不做检测/不拉码流/不跑 CV 模型**；只下命令、收文本、做聚合。任务包见
[tasks/task1](../tasks/task1)，阶段记录见 [phases/P9.md](../phases/P9.md)。

### 11.1 Task 抽象（编排/存储态，非 wire 契约）

一个「协作视频任务」= { 目标 `prompt`、范围 `scope`（路段/摄像头/路口/时间窗/可选目标 hub 集合）、扇出的 N 条命令
（各 `command_id` + `target_agent_id` + 状态）、整体状态、聚合答案 + 证据 }。Task 是编排/存储态，**不进 contracts/**
（wire 契约 envelope/command/`request_video_text` 已存在并复用），落 `backend/anp/video/tasks.py`（`VideoTask`/
`TaskScope`/`TaskCommand` + `SqliteVideoTaskStore`，独立库 `backend/.data/video_tasks.db`）。

- 命令状态：`pending`→`dispatched`→`returned`/`failed`。
- 任务状态：`pending`→`running`（已扇出，等回流）→`aggregated`（全部命令回流，终态）/`failed`。

### 11.2 命令模块注册表

`backend/anp/video/command_modules.py` 声明可下发的「命令模块族」（单一来源，前端与编排器共用 key/展示名）：

| key | 落地 | 对应命令类型 | 说明 |
|---|---|---|---|
| `request_video_text` | ✅ 是 | `request_video_text` | 请求 vision hub 做一次视频推理、回传文本（事件摘要的真实能力，复用 P8 链路） |
| `video.detect` / `video.stream.attach` / `video.model.select` | ⬜ 占位 | （无） | 目标检测/码流接入/CV 模型管理——**vision hub 职责**，ANP 不实现执行端，仅留外形 + 文档，前端诚实标「外部系统(vision hub)」 |

### 11.3 编排：扇出 + 聚合（薄编排）

`backend/anp/video/orchestrator.py`（`VideoTaskOrchestrator`）：

- **扇出** `create_task(prompt, scope)`：选目标 hub 集合（`scope.target_agent_ids` 显式优先，否则默认 roster；设计支持 N>1）
  → 对**每个目标逐条**构造 `request_video_text` 命令（`command_envelope` + 唯一 `command_id` + `target_agent_id`）→ 直发
  `anp.video.command.v1`。**禁 broadcast**：防御性断言命令 wire 无 `broadcast`/`agent_ids` 字段（AGENTS §3.5）。
  视频命令走视频控制层 topic（≠ 交通 `anp.traffic.command.v1`），故**直发、不复用网关交通 `/commands` 管道**，但守其校验纪律。
- **收集 + 聚合** `refresh_task(task_id, aggregate)`：按 `parent_trace_id == command_id` **逐命令归因**回流文本（精确，
  非按内容；store 加了 `parent_trace_id` 可检索列），更新各命令状态；全部命令回流时先用已入库的本地事件生成并缓存规则摘要，
  任务详情默认返回这条本地快路径（不出网）。需要 LLM 精炼时显式用 `?llm=true` 重新合成；结果仍写回任务存储，后续加载走缓存。

### 11.4 网关任务路由（co-host，纯读 + 创建入口）

网关进程 co-host（同 P7 video-text），**不在网关算聚合**（AGENTS §3.4，聚合在编排器）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent-network/video-text/tasks` | body `{prompt, module?, scope}` → 编排器扇出 → 返回 `VideoTask`（含各 `command_id`） |
| GET | `/api/agent-network/video-text/tasks` | 任务列表（批量本地刷新回流状态；全部回流时缓存本地规则摘要，不出网） |
| GET | `/api/agent-network/video-text/tasks/{id}` | 任务详情（归因 + 聚合答案；默认本地快路径，`?llm=true` 可重新调用 LLM 精炼） |
| GET | `/api/agent-network/video-text/command-modules` | 命令模块声明枚举（前端区分可下发/占位） |

### 11.5 前端：问答主界面 + 协作任务侧栏

`?source=gateway` 下 `wm-video-stream` **去 mock 化**：问答升「监控世界模型」主界面（全高聊天，`VideoQAPanel variant=main`）+
右侧协作任务侧栏（`TaskSidebar`：「发布命令/新建任务」表单 + 任务列表，每任务显示参与 hub 及状态/回流进度/聚合答案；点任务把
聚合答案 + 证据回灌主聊天）。三个非问答能力作命令模块入口 / 诚实标「外部系统(vision hub)」。默认非 gateway 的纯 mock 简化壳不动。

### 11.6 运行与验证

```bash
# 端到端冒烟（建任务→扇出定向命令→桩回流→command_id 归因→QA 聚合；N>1 扇出外形无群发）
python backend/scripts/smoke_video_task.py
# live：起 Kafka + 网关（co-host 任务路由）+ 双向桥 + 替身桩 + ingest，前端 ?source=gateway 新建任务
python backend/scripts/run_gateway.py --port 8000
python backend/scripts/run_visionhub_bridge.py
python backend/scripts/stub_visionhub_agent.py
python backend/scripts/run_video_ingest.py
```

- 归因/聚合精确按 `command_id`（R2/R3）；多 hub 各产各事件、键天然不同，不会误并。
- **R1**：真·多机多 hub 端到端依赖 wangxuan 在线（当前 DOWN）→ MVP 用本地替身桩当 ≥1 hub 验证形态，真身多 hub 留待恢复。

## 12. 位置选择器 + 事件数据库可视化（task2）

让问答/建任务更贴合直觉（人不知道某路口有哪些摄像头）+ 让证据可追溯到底层库。任务包
[tasks/task2](../tasks/task2)，阶段记录见 [phases/P9.md](../phases/P9.md)「位置选择器 + 事件数据库」小节。
**只动 `anp/video/` 请求/响应模型，不碰 `contracts/`（不跑 gen_schemas）。**

### 12.1 位置层级（按 vision hub `cameras` 表组织，从 ANP 自己的库派生）

vision hub `cameras` 表层级 `district→intersection_name→primary/secondary_road→camera(camera_position 方位)`，
取摄像头策略「优先 source_id；其次 intersection_name；再次 road_name+camera_position」。本期据此把选择器
组织成 **路口(intersection)→摄像头(方位)+「所有」**：

- ~~位置/摄像头清单只从 ANP 文本库 `distinct_locations()` 派生（不直连 vision hub PG）。~~ **已升级（2026-06-22，对齐 step1）**：
  位置选择器现**优先从摄像头/路口目录 `video_cameras`（由 adapter 同步自 wangxuan，与其 `cameras` 表 1:1）出**，
  再并入文本库派生但目录未覆盖的位置（ANP 自有事件位置不丢）。目录字段（`intersection_name`/`primary_road`/`district`/
  `camera_position`/`source_id`）来自真身，**方位等不再靠 `camera_id` 启发式**。仍守轻数据边界（只摄像头目录、无视频/轨迹）。
  同步机制见 [adapters.md](adapters.md) §5.8（`sync_visionhub_cameras.py`，ssh+psql、DSN 在 `backend/.env`）。
- 分组键：目录路口 `intersection_id=vh-<md5前10>`（由 `intersection_name` 派生）；文本派生组优先 `intersection_id`、
  为 null 按 `road_name` 兜底（`road:<名>`），都 null 归孤儿组。
- 响应模型 `CameraFacet` 加 `source_id`/`name`；`IntersectionFacet` 的 `intersection_name`/`district` 富化位现由目录填实。
  前端 picker 摄像头标签 = `camera_position · #source_id`（目录来源），无则回退 `camera_id` 词缀启发式（纯文本派生时）。
- 「所有摄像头」语义 = 按选中的 `intersection_id` 跨该路口全部摄像头检索，**不外扩到整条道路**。

### 12.2 后端接口（store 派生 + 3 路由）

`SqliteVideoTextStore` 抽 `_where(filters)` 私有方法供 `search/browse/count` 共用，新增 `distinct_locations()` 与
`browse(filters)→(rows,total)`；`SearchFilters` 加 `offset`。响应模型在 `models.py`：`CameraFacet`/`IntersectionFacet`/
`LocationsOut`/`EventRecordOut`(`from_row(row, with_envelope=)`)/`EventBrowseOut`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/agent-network/video-text/locations` | 路口→摄像头层级（含 `event_count`）+ `total_events` |
| GET | `/api/agent-network/video-text/events` | 分页浏览：`limit`(≤`max_query_limit`)/`offset`/`intersection_id`/`camera_id`/`road_name`/`category`/`q`(关键词)/`time_from`/`time_to` → `{total, limit, offset, items}`（列表项**不带 envelope**减重） |
| GET | `/api/agent-network/video-text/events/{event_id}` | 取单条完整记录（**带 envelope**）；不存在 → 404 |

### 12.3 前端

- **`LocationCameraPicker`（可复用）**：路口可搜索 combobox（含「不限路口」+ 清除）+ 摄像头 `<select>`（首项「所有摄像头（该路口全部）」）。切路口同步把摄像头重置为「所有」（不放 effect，避免无限渲染）。导出 `cameraPositionLabel()`。**问答主界面与协作任务侧栏共用同一组件**。
- **事件数据库视图 `EventDatabaseView`**：视图切换 Tab（事件问答 / 事件数据库）。toolbar=位置选择器 + 类别下拉 + 关键词 `q` + 刷新；分页表格（时间/路口·道路/摄像头·方位/类别/置信度/摘要）；行点开**详情抽屉**（全文/摘要/标签/实体/`artifact_ref` 轻指针[标注原始视频不入 ANP，取字节直连 vision hub]/`source_model`/归因命令/可折叠 envelope JSON）。
- **证据跳转**：问答证据 `<li>`→可点 `<button>`，点击 `onOpenEvidence(event_id)` → 切到数据库视图 + `focusEventId={id, nonce}`（nonce 保证重复点同一条也重新定位）→ 取详情、行高亮、滚动到视图。

### 12.4 验证

- 后端 `pytest -q` **132 passed**（+4 新：locations 层级/事件数/排序、按路口浏览+翻页、按摄像头+关键词、取单条+404；不依赖 Kafka/LLM）；`smoke_gateway.py` PASS。
- 前端 `npm run build` PASS；无头 UI `ui-qa-check.mjs` **19/19**（task1 回归）+ `ui-qa-task2.mjs` **8/8**（选择器/搜索/摄像头「所有」/可点证据/证据跳转定位/数据库分页筛选行详情/视图切换/无溢出）。截图 `frontend/public/ui-screenshots/task2-0{1..4}-*.png`。

### 12.5 回流事件↔目录相机对齐（对齐 step2，2026-06-22）

step1 同步了目录（`video_cameras`，1:1 wangxuan）但回流事件还挂不上去——目录相机 `event_count` 恒 0。原因不在数据通路：命令→事件整链**已忠实透传** `camera_id`/`intersection_id`/`road_name`（真身胶水 `run_video_inference_glue._build_result`、替身 `stub_visionhub_agent` 均原样回显，结果桥 `visionhub_result_to_video_text_envelope` 落回 ANP 事件；前端 picker 选目录相机时已把真身 `camera_id`/`intersection_id` 写进 task scope）。**唯一缺口**在计数口径：旧 `catalog_locations()` 路口级事件数按「各相机 `camera_id` 命中之和」算，漏掉带真 `intersection_id` 但 `camera_id` 非目录值的事件（如「所有摄像头」任务回流 `camera_id` 退化为 `unknown-camera`）。

- **计数对齐**（`store.catalog_locations()`，**不改 contracts**）：
  - **相机级** `event_count` 按 `camera_id` 精确命中（目录 `camera_id` 201/201 唯一，整链忠实透传 → 等价于按 source 归属）；
  - **路口级** `event_count` 按 `intersection_id` 归属：事件自带 `intersection_id` 命中目录路口即计入；无 `intersection_id` 但 `camera_id` 属某目录相机则经该相机回溯到其路口（每事件只计一次）。孤儿组无路口键、回退按相机命中之和。
- **发对齐命令**：`run_video_command.py --source-id N` 从目录解析真身 `camera_id`/`intersection_id`/`road_name`（`store.get_camera(source_id)`），一行下发对齐命令；回流文本即挂到目录正确相机/路口。前端建任务走 picker 已自动带真身标识，无需改前端。
- **武汉合成事件不受影响**：ANP 自有合成 `camera_id`（民族大道/雄楚等，天津目录无）经 `/locations` 文本派生路径仍作独立位置展示、不丢、也不污染目录路口。
- **验证**：`pytest` 144 passed（+2：相机级/路口级对齐计数、`get_camera`）；对真实 `backend/.data/video_text.db` 实跑 `catalog_locations()` → 既有 和平路/`vh-60af7e7a8b` 那条事件令该路口 `event_count=1`；命令→命令桥→桩推理→结果桥→入库→目录 在进程内端到端跑通（相机级+路口级均命中，`parent_trace_id==command_id`）。
- **未做（按需续）**：不把 `source_id` 加进 wire 契约（`camera_id` 唯一已够，避免改 contracts/gen_schemas）；经纬度/在线状态等更多轻数据待续传。

### 12.6 真身历史事件回填（对齐 step3，2026-06-22）

step1/step2 只搬了「相机目录」并打通连接，但 ANP 库本身还空（薄黑板靠按需推理喂事件）。step3 把 wangxuan 真身 `events` 表的**历史事件**作轻数据同步进来，库才真正有数据。

- **机制**（镜像 step1 目录同步）：`catalog.py` 加 `fetch_visionhub_events`（ssh+psql 拉 `events` 轻字段 `EVENT_COLUMNS`）+ `map_event`（经 `store.camera_source_index()` 的 source_id→相机/路口映射对齐 + `event_type`→`category`：speeding_vehicle→超速、traffic_congestion→拥堵）；脚本 `sync_visionhub_events.py`（`--limit`/`--event-type`/`--dry-run`）。
- **轻数据边界**：只搬 `description`(文本)/`event_type`/`severity`/`detected_at`/`confidence`/`source_id`/`track_ids`(轻元数据)——**绝不含 bbox/帧/轨迹像素**（重数据留 vision hub）。
- **回填语义**：一次性历史回填**直写 ANP 库**（非经 Kafka；live 回流仍走 Kafka）；幂等键 `message_id=vh-evt-<真身id>`（重跑只增新、`INSERT OR IGNORE`）；身份 `video-perception-visionhub-events-001`（区别 live VLM 回流）。
- **结果（实测）**：同步 **3549 条**（3227 超速 + 322 拥堵）全部经 source_id 对齐到 7 个天津路口/相机；`new=3549 dup=0 bad=0`。live 网关 `/locations` 即见各路口真实事件数（福安大街与荣业大街 1049、桂林路与成都道 658…和平路与哈密道 127），相机挂事件（如 23/23、24/35）。**直读 DB 行、无需重启网关**。
- **性能 caveat**：SQLite 逐行 commit（DELETE journal 每行 fsync）+ 与 live ingest 抢锁 → 3549 条约十余分钟。后续可加批量事务/WAL 优化（本期一次性回填可接受）。
