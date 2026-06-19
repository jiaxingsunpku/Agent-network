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
