"""各 event_type 的 payload 模型 —— envelope 外壳之内、按 topic 区分的负载。

字段定义严格对齐 docs/protocol.md §3/§5 与 docs/world-status.md §2/§3。
这些模型既用于运行时结构化校验，也用于由 scripts/gen_schemas.py 导出 schemas/*.json。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import AckStatus, CommandType, CongestionLevel, Direction


class _Strict(BaseModel):
    """所有 payload 的基类：禁止多余字段，及早暴露契约偏差。"""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# 感知层：观测 payload（topic anp.traffic.perception.observation.v1）
# --------------------------------------------------------------------------- #
class Approach(_Strict):
    """单进口方向的一次采样观测（docs/world-status.md §2）。"""

    direction: Direction
    #: 本采样间隔内**通过**的车辆数（吞吐量，用于换算流量）——不是瞬时车辆数。
    vehicle_count: int = Field(ge=0)
    #: 瞬时**滞留（停车）**车辆数（用于估排队）。
    halting_count: int = Field(ge=0)
    mean_speed_mps: float = Field(ge=0)
    #: 可选；缺省时由系统级智能体按速度推导（docs/world-status.md §4）。
    mean_delay_sec: float | None = Field(default=None, ge=0)


class ObservationPayload(_Strict):
    observation_type: Literal["traffic.intersection"] = "traffic.intersection"
    intersection_id: str = Field(min_length=1)
    approaches: list[Approach] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 感知层（视频域）：视频文本事件 payload（topic anp.video.perception.text.v1，P7）
# --------------------------------------------------------------------------- #
class VideoTextEventPayload(_Strict):
    """视频大模型处理后的「文本事件」（docs/video.md）。

    语义：视频智能体作为感知体上报，**原始视频不进 Kafka**。envelope 已承载
    ``message_id`` / ``source.agent_id`` / ``time.event_ts`` / ``quality.confidence``，
    本 payload 只放视频特有字段，不重复 envelope（docs/naming.md §6）。
    """

    observation_type: Literal["video.text"] = "video.text"
    #: 视频源/摄像头标识。
    camera_id: str = Field(min_length=1)
    #: 路段/路口标识（按路段提问的依据）；road_name 与 intersection_id 至少应有其一。
    road_name: str | None = None
    intersection_id: str | None = None
    road_segment: str | None = None
    #: 视频片段时间范围（可选；事件权威时间走 envelope.time.event_ts）。
    start_ts: str | None = None  # ISO8601 UTC，带 Z
    end_ts: str | None = None
    #: 视频大模型产出的文本描述（检索主体，必填）。
    text: str = Field(min_length=1)
    #: 短摘要（可选，便于命中与展示）。
    summary: str | None = None
    #: 事件类别（拥堵/事故/违章…，自由字符串）。命名避免与 envelope.event_type 撞名。
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    #: 结构化抽取（目标类型/计数/车牌等，自由 dict）。
    entities: dict[str, Any] = Field(default_factory=dict)
    #: 视频片段指针（对象存储 URL/路径等），只存指针不存视频。
    artifact_ref: str | None = None
    #: 产出该文本的视频模型名（可选，溯源用）。
    source_model: str | None = None


# --------------------------------------------------------------------------- #
# 状态层：路口 World Status payload（topic anp.traffic.status.intersection.v1）
# --------------------------------------------------------------------------- #
class StatusWindow(_Strict):
    """滚动窗口元信息（docs/world-status.md §1/§3）。"""

    start: str  # ISO8601 UTC，带 Z
    end: str
    size_sec: int = Field(gt=0)
    sample_count: int = Field(ge=0)


class ApproachStatus(_Strict):
    direction: Direction
    queue_length_m: float = Field(ge=0)
    flow_veh_h: float = Field(ge=0)
    mean_speed_kmh: float = Field(ge=0)


class IntersectionStatusPayload(_Strict):
    status_type: Literal["traffic.intersection"] = "traffic.intersection"
    intersection_id: str = Field(min_length=1)
    window: StatusWindow
    queue_length_m: float = Field(ge=0)
    flow_veh_h: float = Field(ge=0)
    mean_speed_kmh: float = Field(ge=0)
    mean_delay_sec: float = Field(ge=0)
    congestion_level: CongestionLevel
    congestion_index: float = Field(ge=0, le=1)
    approaches: list[ApproachStatus] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 控制层：命令 / ack payload（topic anp.traffic.command.v1 / anp.traffic.ack.v1）
# --------------------------------------------------------------------------- #
class CommandPayload(_Strict):
    command_id: str = Field(min_length=1)
    command_type: CommandType
    params: dict[str, Any] = Field(default_factory=dict)


class SafetyDecision(_Strict):
    """执行端本地 Safety Guard 的判定（权威安全闭环在执行端，docs/protocol.md §5/§7）。"""

    allowed: bool
    decision: str | None = None
    reason: str | None = None


class AckPayload(_Strict):
    command_id: str = Field(min_length=1)  # 回指原命令
    command_type: CommandType
    status: AckStatus
    safety: SafetyDecision | None = None


# --------------------------------------------------------------------------- #
# 通道：agent 在某 topic 上覆盖哪些实体 key（统一世界自描述用）
# --------------------------------------------------------------------------- #
class Channel(_Strict):
    """agent 的一条「通道」声明：它在某个 ``topic`` 上覆盖哪些实体 ``keys``。

    ``keys`` 为空 = 整条 topic、不分实体；非空 = 只覆盖这些实体（如某路口
    ``["intersection_1_1"]``）。Kafka 订阅仍是 topic 级，key 只进声明 / catalog /
    寻址，客户端按 key 过滤留作后续。
    """

    topic: str = Field(min_length=1)
    keys: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 生命周期 / 心跳 payload
# --------------------------------------------------------------------------- #
class AgentLifecyclePayload(_Strict):
    """注册/下线（统一世界名册 anp.world.agent.lifecycle.v1，docs/protocol.md §3）。"""

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)  # 如 "virtual" / "model"
    capabilities: list[str] = Field(default_factory=list)  # 如 ["perception", "exec"]
    command_types: list[str] = Field(default_factory=list)  # 可接收的命令类型
    #: 统一世界通道声明（加法、向后兼容：旧消息无此字段，按默认值 validate）。
    produces: list[Channel] = Field(default_factory=list)  # 本 agent 产出的通道
    consumes: list[Channel] = Field(default_factory=list)  # 本 agent 订阅的通道
    weight: float = Field(default=1.0, ge=0.0)  # 协作权重，先开槽暂不驱动逻辑
    #: model 专用：自报管辖的成员 agent_id（叶子 agent 留空）。前端据此高亮成员、画治理边。
    members: list[str] = Field(default_factory=list)


class AgentHeartbeatPayload(_Strict):
    """心跳/在线状态（topic anp.traffic.agent.heartbeat.v1）。"""

    status: str = Field(min_length=1)  # online | degraded | offline ...
    last_error: str | None = None
