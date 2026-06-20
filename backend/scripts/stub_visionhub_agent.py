#!/usr/bin/env python3
"""vision hub **替身**（P8 step1）：模拟远端视频推理体，桩推理、零真实 VLM。

消费 vision hub info topic ``visionhub.world_model.info.v1``，遇 ``info_type=video_inference_request``
就**桩推理**合成一条文本结果，产到 vision hub 结果 topic ``edge.observation.result.v1``（其原生
``observation.traffic.video_text`` envelope，带 ``trace.correlation_id`` 回指原 ``command_id``）。

它扮演的是**外部系统**（不归 ANP 契约管），故 envelope 字段名用 vision hub 风格、本脚本自带构造，
不复用 ANP builder——这正是 step2 接真实 repo 时要复核对齐的边界（adapter 的 mapping 防御性读取它）。
step1 桩推理产出确定性可读文本，使端到端可断言；真实 VLM / dispatcher 路径见 phases/P8.md step2。

前置：Kafka 已起、topic 已建。可与 ``run_visionhub_bridge.py`` 同机跑（同一本地 broker）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/stub_visionhub_agent.py
    stub_visionhub_agent.py --from-beginning   # 重放既往未处理命令
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.adapters.visionhub import (  # noqa: E402
    VISIONHUB_RESULT_TOPIC,
    VisionHubBridgeConfig,
    ensure_visionhub_topics,
)
from anp.adapters.visionhub.config import VISIONHUB_INFO_TYPE, VISIONHUB_VIDEO_TEXT_EVENT_TYPE  # noqa: E402
from anp.contracts import new_message_id, new_trace_id, now_iso  # noqa: E402
from anp.messaging import make_consumer, make_producer  # noqa: E402

#: 桩推理的「视频模型」标识（溯源用，区别于真实 VLM）。
STUB_MODEL = "visionhub-stub-vlm"
#: 提问关键词 → (类别, 标签, 合成观察) 的简易规则，使桩文本随 prompt 变化、可读、可断言。
_KEYWORD_RULES: list[tuple[tuple[str, ...], str, list[str], str]] = [
    (("事故", "碰撞", "追尾", "刮擦"), "事故", ["事故", "追尾"], "两车追尾，右侧一条车道受阻，已有人员下车查看。"),
    (("拥堵", "堵", "排队", "缓行"), "拥堵", ["拥堵", "排队"], "车流缓行，路口西进口排队约 12 辆，信号灯放行偏慢。"),
    (("违章", "闯红灯", "压线", "逆行"), "违章", ["违章"], "一辆白色轿车闯红灯通过路口，未见执法跟进。"),
    (("施工", "占道", "围挡"), "施工", ["施工", "占道"], "右侧车道围挡施工，车辆向左并线，通行能力下降。"),
]
_DEFAULT_RESULT = ("正常", ["畅通"], "车流通畅，无明显事故、拥堵或违章，路口运行正常。")


def stub_infer(info_payload: dict[str, Any]) -> dict[str, Any]:
    """桩推理：从 info 请求合成一条 vision hub 风格的文本结果 payload。"""

    prompt = str(info_payload.get("prompt") or "")
    road = info_payload.get("road_name") or "该路段"
    camera = info_payload.get("camera_id") or "unknown-camera"
    category, tags, observation = _DEFAULT_RESULT
    for keys, cat, tg, obs in _KEYWORD_RULES:
        if any(k in prompt for k in keys):
            category, tags, observation = cat, tg, obs
            break
    text = f"{road}（摄像头 {camera}）视频分析：{observation}"
    summary = f"{road}{category}"
    return {
        "camera_id": camera,
        "road_name": info_payload.get("road_name"),
        "intersection_id": info_payload.get("intersection_id"),
        "road_segment": info_payload.get("road_segment"),
        "text": text,
        "summary": summary,
        "category": category,
        "tags": tags,
        "entities": {"vehicle_count": 12} if category in ("拥堵", "事故") else {},
        "artifact_ref": info_payload.get("clip_ref"),
        "source_model": STUB_MODEL,
        "confidence": 0.9,
        "command_id": info_payload.get("command_id"),  # 冗余回带，便于审计
    }


def build_result_envelope(info_value: dict[str, Any], result_payload: dict[str, Any]) -> dict[str, Any]:
    """组装 vision hub 原生结果 envelope（替身扮演外部系统，自带 envelope 构造）。"""

    info_payload = info_value.get("payload") or {}
    trace = info_value.get("trace") or {}
    correlation_id = trace.get("correlation_id") or info_payload.get("command_id")
    return {
        "schema_version": "1.0",
        "message_id": new_message_id(),
        "event_type": VISIONHUB_VIDEO_TEXT_EVENT_TYPE,
        "source": {"system": "vision_hub", "agent_id": info_payload.get("target_agent_id") or "video-visionhub-001"},
        "time": {"event_ts": now_iso()},
        "scope": {
            "camera_id": result_payload.get("camera_id"),
            "road_name": result_payload.get("road_name"),
            "intersection_id": result_payload.get("intersection_id"),
        },
        "payload": result_payload,
        "trace": {"trace_id": new_trace_id(), "correlation_id": correlation_id},
    }


def handle_info(value: Any, producer, result_topic: str) -> dict[str, Any] | None:
    """处理一条 info 消息：仅 video_inference_request → 桩推理 → 产结果。返回结果 envelope 或 None。"""

    if not isinstance(value, dict):
        return None
    payload = value.get("payload") or {}
    if payload.get("info_type") != VISIONHUB_INFO_TYPE:
        return None
    result_payload = stub_infer(payload)
    result_env = build_result_envelope(value, result_payload)
    cid = result_env["trace"].get("correlation_id")
    producer.send(result_topic, key=cid, value=result_env)
    return result_env


def main() -> int:
    ap = argparse.ArgumentParser(description="vision hub 替身（P8 step1 桩推理）。")
    ap.add_argument("--bootstrap", default=None, help="vision hub Kafka bootstrap（step1 默认本地）")
    ap.add_argument("--from-beginning", action="store_true", help="从最早重放 info（默认只收新消息）")
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒），默认永久")
    args = ap.parse_args()

    cfg = VisionHubBridgeConfig(visionhub_bootstrap=args.bootstrap)
    if args.bootstrap is None:  # step1 本地：确保 vision hub 外部 topic 存在
        ensure_visionhub_topics(cfg)
    producer = make_producer(bootstrap_servers=cfg.visionhub_bootstrap)
    consumer = make_consumer(
        cfg.info_topic,
        group_id="visionhub-stub-agent",
        bootstrap_servers=cfg.visionhub_bootstrap,
        auto_offset_reset="earliest" if args.from_beginning else "latest",
        consumer_timeout_ms=1000,
    )
    print(f"[stub-visionhub] 监听 {cfg.info_topic} → 桩推理 → {VISIONHUB_RESULT_TOPIC}")
    import time

    deadline = None if args.duration is None else time.monotonic() + args.duration
    produced = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            for record in consumer:
                res = handle_info(record.value, producer, cfg.result_topic)
                if res is not None:
                    produced += 1
                    p = res["payload"]
                    print(f"[stub-visionhub] cmd={str(p.get('command_id'))[:8]} → {p.get('category')}: {p.get('text')[:48]}…")
                    producer.flush()
                if deadline is not None and time.monotonic() >= deadline:
                    break
    except KeyboardInterrupt:
        print("\n[stub-visionhub] 收到中断，停止。")
    finally:
        consumer.close()
        producer.flush()
        producer.close()
    print(f"[stub-visionhub] 退出。produced={produced}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
