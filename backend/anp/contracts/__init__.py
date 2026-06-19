"""唯一契约源（docs/architecture.md §4）。

对外只从这里导入 envelope / topic / 枚举 / payload / builder，
不要深入子模块路径，也不要在别处复刻这些定义。
"""

from __future__ import annotations

from .enums import (
    AckStatus,
    AgentRole,
    CommandType,
    CongestionLevel,
    Direction,
    EventType,
    SourceSystem,
)
from .envelope import (
    SCHEMA_VERSION,
    Envelope,
    Quality,
    Scope,
    SequenceGenerator,
    Source,
    Target,
    TimeInfo,
    Trace,
    ack_envelope,
    command_envelope,
    expires_at_iso,
    iso_utc,
    make_envelope,
    new_message_id,
    new_trace_id,
    now_iso,
    parse_iso,
    observation_envelope,
    parse_payload,
    partition_key,
    status_envelope,
    video_text_envelope,
)
from .payloads import (
    AckPayload,
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Approach,
    ApproachStatus,
    CommandPayload,
    IntersectionStatusPayload,
    ObservationPayload,
    SafetyDecision,
    StatusWindow,
    VideoTextEventPayload,
)
from .topics import (
    ALL_TRAFFIC_TOPICS,
    ALL_VIDEO_TOPICS,
    TOPIC_PREFIX,
    Domain,
    Layer,
    TrafficTopics,
    VideoTopics,
    build_topic,
)

__all__ = [
    # enums
    "AckStatus",
    "AgentRole",
    "CommandType",
    "CongestionLevel",
    "Direction",
    "EventType",
    "SourceSystem",
    # envelope shell + 子模型
    "SCHEMA_VERSION",
    "Envelope",
    "Source",
    "Target",
    "TimeInfo",
    "Scope",
    "Quality",
    "Trace",
    "SequenceGenerator",
    # 时间 / id 工具
    "now_iso",
    "iso_utc",
    "parse_iso",
    "expires_at_iso",
    "new_message_id",
    "new_trace_id",
    # builder / 解析 / 分区键
    "make_envelope",
    "observation_envelope",
    "status_envelope",
    "video_text_envelope",
    "command_envelope",
    "ack_envelope",
    "parse_payload",
    "partition_key",
    # payloads
    "ObservationPayload",
    "Approach",
    "IntersectionStatusPayload",
    "StatusWindow",
    "ApproachStatus",
    "VideoTextEventPayload",
    "CommandPayload",
    "SafetyDecision",
    "AckPayload",
    "AgentLifecyclePayload",
    "AgentHeartbeatPayload",
    # topics
    "TOPIC_PREFIX",
    "Domain",
    "Layer",
    "build_topic",
    "TrafficTopics",
    "ALL_TRAFFIC_TOPICS",
    "VideoTopics",
    "ALL_VIDEO_TOPICS",
]
