"""视频文本问答 API 的请求/响应模型（P7）。

响应外形对齐老前端 ``QueryResponse``（``answer`` / ``tool_calls`` / ``evidence`` /
``warnings``），便于前端复用展示思想。HTTP ingest 请求 :class:`VideoTextEventIn`
负责把视频特有字段 + 可选 envelope 级字段组装成统一 envelope。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from anp.contracts import (
    Envelope,
    Quality,
    VideoTextEventPayload,
    video_text_envelope,
)

from .command_modules import REQUEST_VIDEO_TEXT
from .tasks import TaskScope


class VideoTextEventIn(BaseModel):
    """HTTP 入库请求：视频大模型处理后的一条文本事件。"""

    model_config = ConfigDict(extra="forbid")

    # 视频特有字段
    camera_id: str = Field(min_length=1)
    road_name: str | None = None
    intersection_id: str | None = None
    road_segment: str | None = None
    start_ts: str | None = None
    end_ts: str | None = None
    text: str = Field(min_length=1)
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    artifact_ref: str | None = None
    source_model: str | None = None
    # envelope 级（可选；缺省由服务端兜底）
    source_agent_id: str | None = None
    event_ts: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    message_id: str | None = None

    def to_envelope(self, *, default_agent_id: str) -> Envelope:
        payload = VideoTextEventPayload(
            camera_id=self.camera_id,
            road_name=self.road_name,
            intersection_id=self.intersection_id,
            road_segment=self.road_segment,
            start_ts=self.start_ts,
            end_ts=self.end_ts,
            text=self.text,
            summary=self.summary,
            category=self.category,
            tags=list(self.tags),
            entities=dict(self.entities),
            artifact_ref=self.artifact_ref,
            source_model=self.source_model,
        )
        quality = Quality(confidence=self.confidence) if self.confidence is not None else None
        return video_text_envelope(
            agent_id=self.source_agent_id or default_agent_id,
            payload=payload,
            event_ts=self.event_ts,
            quality=quality,
            message_id=self.message_id,
        )


class VideoTextQueryRequest(BaseModel):
    """检索问答请求：自然语言问题 + 可选结构化过滤。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    time_from: str | None = None
    time_to: str | None = None
    road_name: str | None = None
    intersection_id: str | None = None
    camera_id: str | None = None
    category: str | None = None
    keywords: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=100)


# --- 响应模型（对齐老前端 QueryResponse） ---
class EvidenceItem(BaseModel):
    event_id: str
    event_ts: str | None = None
    camera_id: str | None = None
    road_name: str | None = None
    intersection_id: str | None = None
    category: str | None = None
    summary: str | None = None
    text: str
    confidence: float | None = None
    artifact_ref: str | None = None


class ToolCallResult(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class QueryResponse(BaseModel):
    answer: str
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class IngestResponse(BaseModel):
    event_id: str
    stored: bool  # True=新写入，False=去重命中
    count: int  # 库内当前事件总数


# --- 位置枚举 + 数据库浏览（task2，前端位置选择器 + 事件数据库视图） ---
class CameraFacet(BaseModel):
    """路口下的一个摄像头（含事件数）。

    对齐 wangxuan visionhub `cameras` 后：``source_id`` 为真身稳定键（整数），
    ``camera_position`` 为方位（东北角/西南角…），``name`` 为真身原名（可作 tooltip）。
    目录来源时三者有值；纯文本事件派生时仅 ``camera_id``+``event_count``（其余留空）。
    """

    camera_id: str
    source_id: int | None = None
    name: str | None = None
    camera_position: str | None = None
    event_count: int


class IntersectionFacet(BaseModel):
    """一个路口聚合（其下摄像头 + 事件数）。

    ``intersection_name``/``district`` 为对齐 vision hub ``cameras`` 表的富化预留位
    （ANP 文本库现无此列，留空），前端有则优先展示、无则回退 ``road_name``/``intersection_id``。
    """

    intersection_id: str | None = None
    intersection_name: str | None = None
    road_name: str | None = None
    district: str | None = None
    event_count: int
    cameras: list[CameraFacet] = Field(default_factory=list)


class LocationsOut(BaseModel):
    """位置枚举响应：库内实有路口层级 + 事件总数。"""

    intersections: list[IntersectionFacet] = Field(default_factory=list)
    total_events: int


class EventRecordOut(BaseModel):
    """数据库视图的一条完整记录。``envelope`` 仅取单条详情时带（列表省略以减重）。"""

    event_id: str
    event_ts: str | None = None
    source_agent_id: str | None = None
    confidence: float | None = None
    parent_trace_id: str | None = None
    camera_id: str | None = None
    road_name: str | None = None
    intersection_id: str | None = None
    road_segment: str | None = None
    start_ts: str | None = None
    end_ts: str | None = None
    text: str
    summary: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    artifact_ref: str | None = None
    source_model: str | None = None
    envelope: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any], *, with_envelope: bool = False) -> "EventRecordOut":
        """从 store 行字典构造（tags/entities/envelope 已是 Python 对象）。"""

        return cls(
            event_id=row.get("event_id"),
            event_ts=row.get("event_ts"),
            source_agent_id=row.get("source_agent_id"),
            confidence=row.get("confidence"),
            parent_trace_id=row.get("parent_trace_id"),
            camera_id=row.get("camera_id"),
            road_name=row.get("road_name"),
            intersection_id=row.get("intersection_id"),
            road_segment=row.get("road_segment"),
            start_ts=row.get("start_ts"),
            end_ts=row.get("end_ts"),
            text=row.get("text") or "",
            summary=row.get("summary"),
            category=row.get("category"),
            tags=list(row.get("tags") or []),
            entities=dict(row.get("entities") or {}),
            artifact_ref=row.get("artifact_ref"),
            source_model=row.get("source_model"),
            envelope=row.get("envelope") if with_envelope else None,
        )


class EventBrowseOut(BaseModel):
    """数据库浏览分页响应。"""

    total: int
    limit: int
    offset: int
    items: list[EventRecordOut] = Field(default_factory=list)


# --- 协作视频任务（P9） ---
class TaskCreateRequest(BaseModel):
    """新建协作视频任务请求：目标 prompt + 命令模块 + 范围（路段/摄像头/目标 hub 集合）。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    #: 命令模块 key（MVP 仅 request_video_text 可下发；占位模块返回 400）。
    module: str = REQUEST_VIDEO_TEXT
    scope: TaskScope = Field(default_factory=TaskScope)


class CommandModuleOut(BaseModel):
    """命令模块声明（前端枚举：哪些可下发、哪些是 vision hub 占位）。"""

    key: str
    title: str
    description: str
    implemented: bool
    command_type: str | None = None
