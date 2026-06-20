"""ANP 契约 ↔ vision hub 原生 envelope 的纯映射（P8，无 IO、可单测）。

adapter 是唯一翻译点（docs/adapters.md §5）。两个方向：

1. **命令方向**：ANP ``anp.video.command.v1``（``CommandPayload``，``command_type=request_video_text``）
   → vision hub ``visionhub.world_model.info.v1`` 的 world-model info 消息
   （``info_type=video_inference_request``）。
2. **结果方向**：vision hub ``edge.observation.result.v1``（``observation.traffic.video_text``）
   → ANP ``VideoTextEventPayload``，经 ``video_text_envelope`` 装回 ANP 感知层（P7 ingest 零改入库）。

vision hub envelope 约定形如（phases/P8.md 勘察）::

    {schema_version, message_id, event_type, source{system,agent_id}, time{event_ts},
     scope{...}, payload{...}, trace{trace_id, correlation_id}}

本映射对其字段**防御性读取**（多备选字段名 + 缺省兜底），step2 接真实 repo 时按其当前模块复核字段
（docs/adapters.md §5、phases/P8.md 风险）。**关联键**统一用 ANP ``command_id``：命令方向写入
vision hub ``trace.correlation_id``；结果方向从 ``trace.correlation_id`` 读回，落到 ANP 文本事件
envelope 的 ``trace.parent_trace_id``，使「命令↔回流文本」可追溯（CommandTracker 据此记账）。
"""

from __future__ import annotations

from typing import Any

from anp.contracts import (
    CommandPayload,
    CommandType,
    Envelope,
    EventType,
    Quality,
    SCHEMA_VERSION,
    Trace,
    VideoTextEventPayload,
    new_message_id,
    new_trace_id,
    now_iso,
    parse_payload,
    video_text_envelope,
)

from .config import VISIONHUB_INFO_TYPE, VISIONHUB_VIDEO_TEXT_EVENT_TYPE


# --------------------------------------------------------------------------- #
# 命令方向：ANP 命令 envelope → vision hub world-model info 消息（dict）
# --------------------------------------------------------------------------- #
def anp_command_to_visionhub_info(
    env: Envelope,
    *,
    source_agent_id: str,
    requester: str = "anp",
    info_type: str = VISIONHUB_INFO_TYPE,
) -> dict[str, Any] | None:
    """把 ANP「请求视频推理」命令译成 vision hub info 消息。

    非命令 / 非 ``request_video_text`` 返回 ``None``（不是本桥关心的消息）。
    关联键 = ``command_id`` → 写入 ``trace.correlation_id``，供结果回流时对账。
    """

    if env.event_type != EventType.COMMAND:
        return None
    try:
        payload = parse_payload(env)
    except Exception:  # noqa: BLE001 - 命令外形非法，丢弃
        return None
    if not isinstance(payload, CommandPayload) or payload.command_type != CommandType.REQUEST_VIDEO_TEXT:
        return None

    params = dict(payload.params or {})
    command_id = payload.command_id
    info_payload = {
        "info_type": info_type,
        "requester": requester,
        "command_id": command_id,
        "target_agent_id": env.target.agent_id,
        "camera_id": params.get("camera_id"),
        "road_name": params.get("road_name"),
        "intersection_id": params.get("intersection_id"),
        "road_segment": params.get("road_segment"),
        "time_window": params.get("time_window"),
        "prompt": params.get("prompt"),
        "clip_ref": params.get("clip_ref"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": new_message_id(),
        "event_type": "world_model.info",
        "source": {"system": requester, "agent_id": source_agent_id},
        "time": {"event_ts": env.time.event_ts or now_iso()},
        "scope": {
            "camera_id": params.get("camera_id"),
            "road_name": params.get("road_name"),
            "intersection_id": params.get("intersection_id"),
        },
        "payload": info_payload,
        "trace": {"trace_id": env.trace.trace_id, "correlation_id": command_id},
    }


# --------------------------------------------------------------------------- #
# 结果方向：vision hub edge 观测结果（dict）→ ANP 视频文本事件 envelope
# --------------------------------------------------------------------------- #
def _first(d: dict[str, Any], *keys: str) -> Any:
    """取第一个非空字段（防御外部字段名差异）。"""

    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def visionhub_result_to_video_text_envelope(
    value: Any,
    *,
    perception_agent_id: str,
) -> Envelope | None:
    """把 vision hub ``observation.traffic.video_text`` 结果译成 ANP 视频文本事件 envelope。

    不是该类结果 / 无正文文本 → ``None``（跳过）。``trace.correlation_id``（= 原 ``command_id``）
    落到 ANP envelope 的 ``trace.parent_trace_id``。失败/字段缺失尽量兜底，不抛异常。
    """

    if not isinstance(value, dict):
        return None
    if value.get("event_type") != VISIONHUB_VIDEO_TEXT_EVENT_TYPE:
        return None
    payload = value.get("payload")
    if not isinstance(payload, dict):
        return None

    text = _first(payload, "text", "description", "summary")
    if not text:
        return None  # 无正文不入库（VideoTextEventPayload.text 必填）

    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    camera_id = _first(payload, "camera_id") or _first(scope, "camera_id") or "unknown-camera"
    tags = payload.get("tags")
    entities = payload.get("entities")
    confidence = payload.get("confidence")

    vt_payload = VideoTextEventPayload(
        camera_id=str(camera_id),
        road_name=_first(payload, "road_name") or _first(scope, "road_name"),
        intersection_id=_first(payload, "intersection_id") or _first(scope, "intersection_id"),
        road_segment=_first(payload, "road_segment"),
        start_ts=_first(payload, "start_ts"),
        end_ts=_first(payload, "end_ts"),
        text=str(text),
        summary=_first(payload, "summary"),
        category=_first(payload, "category"),
        tags=list(tags) if isinstance(tags, list) else [],
        entities=dict(entities) if isinstance(entities, dict) else {},
        artifact_ref=_first(payload, "artifact_ref", "clip_ref"),
        source_model=_first(payload, "source_model", "model"),
    )

    trace = value.get("trace") if isinstance(value.get("trace"), dict) else {}
    correlation_id = trace.get("correlation_id") or _first(payload, "command_id")
    event_ts = (value.get("time") or {}).get("event_ts") if isinstance(value.get("time"), dict) else None
    quality = Quality(confidence=confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None

    return video_text_envelope(
        agent_id=perception_agent_id,
        payload=vt_payload,
        event_ts=event_ts,
        quality=quality,
        trace=Trace(trace_id=new_trace_id(), parent_trace_id=correlation_id),
    )
