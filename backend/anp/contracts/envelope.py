"""统一消息 Envelope 与构造/解析工具 —— 全平台唯一 envelope 来源。

所有层、所有消息共用同一 envelope 外壳，差异只在 ``event_type`` 与 ``payload``
（docs/protocol.md §1）。任何地方都不得另起字段名或私自加层级；构造消息一律走
本模块的 builder，不要手搓 dict。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .enums import EventType, SourceSystem
from .payloads import (
    AckPayload,
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    CommandPayload,
    IntersectionStatusPayload,
    ObservationPayload,
    VideoTextEventPayload,
)

#: envelope.schema_version（docs/protocol.md §1）。不兼容变更才升主版本。
SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------- #
# 时间 / id 工具
# --------------------------------------------------------------------------- #
def iso_utc(dt: datetime) -> str:
    """转成 ISO8601 UTC、带 ``Z`` 的毫秒精度字符串（docs/protocol.md §2）。

    全平台时间戳格式的唯一来源——窗口边界、事件时间等都走这里，避免各处自拼格式。
    """

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(ts: str) -> datetime:
    """解析 ISO8601 UTC（带 ``Z`` 或显式偏移）为带 tz 的 ``datetime``。

    与 :func:`iso_utc` 互逆，供系统级智能体按 ``event_ts`` 切窗口用。
    """

    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


#: 旧别名，保留内部引用。
_to_iso = iso_utc


def now_iso() -> str:
    """当前 UTC 时间（仅用于无权威事件时间时兜底；observation 应传真实 ``event_ts``）。"""

    return iso_utc(datetime.now(timezone.utc))


def expires_at_iso(expires_in_sec: float, from_ts: datetime | None = None) -> str:
    """由 ``expires_in_sec`` 计算命令的 ``expires_at``（docs/protocol.md §5、gateway-api.md §3）。"""

    base = from_ts or datetime.now(timezone.utc)
    return iso_utc(base + timedelta(seconds=expires_in_sec))


def new_message_id() -> str:
    return str(uuid4())


def new_trace_id() -> str:
    return str(uuid4())


class SequenceGenerator:
    """每个发送方维护一个单调递增 ``sequence``（docs/protocol.md §4.2）。"""

    __slots__ = ("_n",)

    def __init__(self, start: int = 0) -> None:
        self._n = start

    def next(self) -> int:
        n = self._n
        self._n += 1
        return n


# --------------------------------------------------------------------------- #
# Envelope 子模型
# --------------------------------------------------------------------------- #
class _Frame(BaseModel):
    """envelope 外壳各子结构基类：字段固定、禁止多余字段。"""

    model_config = ConfigDict(extra="forbid")


class Source(_Frame):
    system: SourceSystem
    agent_id: str = Field(min_length=1)
    gateway_id: str | None = None  # 经网关时填


class Target(_Frame):
    """上行观测/状态留空（两字段皆 None）；命令必填 ``agent_id``。"""

    agent_id: str | None = None
    region_id: str | None = None


class TimeInfo(_Frame):
    event_ts: str  # 权威事件时间，ISO8601 UTC 带 Z
    sequence: int = Field(default=0, ge=0)
    expires_at: str | None = None  # 命令用，过期拒收


class Scope(_Frame):
    site_id: str | None = None
    region_id: str | None = None
    object_id: str | None = None  # 被描述实体，如 intersection_id


class Quality(_Frame):
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    data_latency_ms: int = Field(default=0, ge=0)


class Trace(_Frame):
    trace_id: str
    parent_trace_id: str | None = None


class Envelope(_Frame):
    """统一消息外壳（docs/protocol.md §1）。``payload`` 按 topic 结构不同，

    用 :func:`parse_payload` 还原为对应的强类型 payload 模型。
    """

    schema_version: str = SCHEMA_VERSION
    message_id: str
    event_type: EventType
    source: Source
    target: Target = Field(default_factory=Target)
    time: TimeInfo
    scope: Scope = Field(default_factory=Scope)
    payload: dict[str, Any] = Field(default_factory=dict)
    quality: Quality = Field(default_factory=Quality)
    trace: Trace = Field(default_factory=lambda: Trace(trace_id=new_trace_id()))

    def to_wire(self) -> dict[str, Any]:
        """序列化为可直接 json.dumps 的 dict（含显式 null，便于消费侧稳定解析）。"""

        return self.model_dump(mode="json")


# --------------------------------------------------------------------------- #
# event_type → payload 模型映射；解析与分区键
# --------------------------------------------------------------------------- #
_PAYLOAD_BY_EVENT: dict[EventType, type[BaseModel]] = {
    EventType.OBSERVATION_TRAFFIC_INTERSECTION: ObservationPayload,
    EventType.STATUS_TRAFFIC_INTERSECTION: IntersectionStatusPayload,
    EventType.OBSERVATION_VIDEO_TEXT: VideoTextEventPayload,
    EventType.COMMAND: CommandPayload,
    EventType.COMMAND_ACK: AckPayload,
    EventType.AGENT_REGISTERED: AgentLifecyclePayload,
    EventType.AGENT_DEREGISTERED: AgentLifecyclePayload,
    EventType.AGENT_HEARTBEAT: AgentHeartbeatPayload,
}


def parse_payload(env: Envelope) -> BaseModel:
    """按 ``event_type`` 把 ``env.payload`` 还原为强类型 payload 模型并校验。"""

    model = _PAYLOAD_BY_EVENT.get(env.event_type)
    if model is None:  # pragma: no cover - 防御性
        raise ValueError(f"未知 event_type，无对应 payload 模型: {env.event_type}")
    return model.model_validate(env.payload)


def partition_key(env: Envelope) -> str:
    """生产者分区键（docs/protocol.md §4.1、naming.md §3）。

    状态层按被聚合实体 ``object_id``（intersection_id）切分；其余按 ``source.agent_id``。
    """

    if env.event_type == EventType.STATUS_TRAFFIC_INTERSECTION:
        return env.scope.object_id or env.source.agent_id
    return env.source.agent_id


# --------------------------------------------------------------------------- #
# Builder —— 别处一律走这里，不要手搓 envelope
# --------------------------------------------------------------------------- #
def make_envelope(
    *,
    event_type: EventType,
    source: Source,
    payload: BaseModel | dict[str, Any],
    target: Target | None = None,
    scope: Scope | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    expires_at: str | None = None,
    quality: Quality | None = None,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """通用 envelope 构造。``payload`` 传 payload 模型实例（推荐）或已校验好的 dict。"""

    payload_dict = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    return Envelope(
        message_id=message_id or new_message_id(),
        event_type=event_type,
        source=source,
        target=target or Target(),
        time=TimeInfo(event_ts=event_ts or now_iso(), sequence=sequence, expires_at=expires_at),
        scope=scope or Scope(),
        payload=payload_dict,
        quality=quality or Quality(),
        trace=trace or Trace(trace_id=new_trace_id()),
    )


def observation_envelope(
    *,
    agent_id: str,
    payload: ObservationPayload,
    site_id: str | None = None,
    region_id: str | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    quality: Quality | None = None,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """感知体上报观测（topic anp.traffic.perception.observation.v1）。"""

    return make_envelope(
        event_type=EventType.OBSERVATION_TRAFFIC_INTERSECTION,
        source=Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id),
        payload=payload,
        scope=Scope(site_id=site_id, region_id=region_id, object_id=payload.intersection_id),
        event_ts=event_ts,
        sequence=sequence,
        quality=quality,
        trace=trace,
        message_id=message_id,
    )


def video_text_envelope(
    *,
    agent_id: str,
    payload: VideoTextEventPayload,
    site_id: str | None = None,
    region_id: str | None = None,
    object_id: str | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    quality: Quality | None = None,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """视频感知体上报文本事件（topic anp.video.perception.text.v1，docs/video.md）。

    ``scope.object_id`` 默认取路口/路段/摄像头之一，保证同一实体消息分区稳定。
    """

    return make_envelope(
        event_type=EventType.OBSERVATION_VIDEO_TEXT,
        source=Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id),
        payload=payload,
        scope=Scope(
            site_id=site_id,
            region_id=region_id,
            object_id=object_id or payload.intersection_id or payload.road_name or payload.camera_id,
        ),
        event_ts=event_ts,
        sequence=sequence,
        quality=quality,
        trace=trace,
        message_id=message_id,
    )


def status_envelope(
    *,
    agent_id: str,
    payload: IntersectionStatusPayload,
    site_id: str | None = None,
    region_id: str | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """系统级智能体产出 World Status（topic anp.traffic.status.intersection.v1）。"""

    return make_envelope(
        event_type=EventType.STATUS_TRAFFIC_INTERSECTION,
        source=Source(system=SourceSystem.PLATFORM, agent_id=agent_id),
        payload=payload,
        scope=Scope(site_id=site_id, region_id=region_id, object_id=payload.intersection_id),
        event_ts=event_ts,
        sequence=sequence,
        trace=trace,
        message_id=message_id,
    )


def command_envelope(
    *,
    source: Source,
    target_agent_id: str,
    payload: CommandPayload,
    expires_at: str,
    site_id: str | None = None,
    region_id: str | None = None,
    object_id: str | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """下行命令（topic anp.traffic.command.v1）。``target_agent_id`` 必填、禁止 broadcast。"""

    return make_envelope(
        event_type=EventType.COMMAND,
        source=source,
        payload=payload,
        target=Target(agent_id=target_agent_id, region_id=region_id),
        scope=Scope(site_id=site_id, region_id=region_id, object_id=object_id),
        event_ts=event_ts,
        sequence=sequence,
        expires_at=expires_at,
        trace=trace,
        message_id=message_id,
    )


def ack_envelope(
    *,
    agent_id: str,
    payload: AckPayload,
    target_agent_id: str | None = None,
    event_ts: str | None = None,
    sequence: int = 0,
    trace: Trace | None = None,
    message_id: str | None = None,
) -> Envelope:
    """执行端回执（topic anp.traffic.ack.v1）。"""

    return make_envelope(
        event_type=EventType.COMMAND_ACK,
        source=Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id),
        payload=payload,
        target=Target(agent_id=target_agent_id),
        event_ts=event_ts,
        sequence=sequence,
        trace=trace,
        message_id=message_id,
    )
