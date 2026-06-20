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
POST /events    入库一条视频文本事件（VideoTextEventIn）→ {event_id, stored, count}
POST /query     检索 + 问答（VideoTextQueryRequest）→ QueryResponse
GET  /health    {ok, service, count, llm_enabled}
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
- step2 跨机（wangxuan `/nvme2/VLM/agents_for_vision_hub`）主风险是**跨机 Kafka**（ANP broker advertised 仅 localhost，需配双 listener/开端口/隧道）+ vision hub 侧补「收命令→dispatch→产结果」胶水 + 真实字段复核。详见 [phases/P8.md](../phases/P8.md)。


### 10.6 step2 跨机落地（✅ 2026-06-20 已跨机活体验证）

vision hub 真身在 wangxuan（docker 容器，`network_mode:host`，PG/Milvus/Redis 在跑）。step2 以**宿主机旁路
sidecar**（`scripts/run_video_inference_glue.py`）打通：消费 `visionhub.world_model.info.v1` → 调其
`POST /api/v1/world-model/demo-dispatch` 做**真实多智能体推理**（qwen-plus）→ 轮询 `final_answer` → 直接产
`edge.observation.result.v1`（`correlation_id=command_id`）→ ANP 结果桥译回入库问答。**vision hub 容器零改
零重启**；跨机 Kafka 走**反向 SSH 隧道 + 单 broker**。活体 PASS：命令(人民路)→真实推理→回流入库→问答命中，
关联键三处一致，视频组容器未受影响。详见 [phases/P8.md](../phases/P8.md) §step2 与 [adapters.md](adapters.md) §5.6。
