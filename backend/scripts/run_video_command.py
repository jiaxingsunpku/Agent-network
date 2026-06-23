#!/usr/bin/env python3
"""CLI：下发一条「请求视频推理」命令到 ANP 视频控制层（P8 step1）。

发 ``anp.video.command.v1``（``command_type=request_video_text``）→ 由 ``run_visionhub_bridge.py``
的命令桥译给 vision hub → vision hub（step1 替身脚本）推理产文本 → 结果桥译回 ANP 入库 → 问答。

命令与问答**解耦**（异步黑板）：本脚本只负责发命令并打印 ``command_id``（= 关联键），回流文本
何时到、问答何时查由各自进程节奏决定。不经网关/registry（P8 决策，step1 最简）。

前置：Kafka 已起、topic 已建（含 anp.video.command.v1）。

用法::

    # 对齐 step2：按真身 source_id 发对齐命令（回流事件自动挂到目录正确相机/路口）
    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_video_command.py \
        --source-id 4 --prompt "该路口最近有没有事故或拥堵？"

    # 或显式给标识（合成 id，回流事件作 ANP 自有位置，不挂目录）
    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_video_command.py \
        --camera-id cam-minzu-east-001 --road-name 民族大道 \
        --prompt "民族大道与雄楚大道交叉口最近有没有事故或拥堵？" --time-from 2026-06-13T04:00:00Z
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.adapters.visionhub import VISIONHUB_AGENT_ID  # noqa: E402
from anp.contracts import (  # noqa: E402
    CommandPayload,
    CommandType,
    Source,
    SourceSystem,
    VideoTopics,
    command_envelope,
    expires_at_iso,
    new_message_id,
)
from anp.messaging import make_producer, publish  # noqa: E402
from anp.video.config import VIDEO_TASK_AGENT_ID, get_video_config  # noqa: E402
from anp.video.store import SqliteVideoTextStore  # noqa: E402


def _resolve_source(source_id: int) -> dict | None:
    """从本地目录（对齐 wangxuan 的 ``video_cameras``）按 source_id 解析真身标识。

    返回 ``{camera_id, intersection_id, road_name}``（缺失留空）；目录无此 source 返回 None。
    """

    store = SqliteVideoTextStore(get_video_config().db_path)
    cam = store.get_camera(source_id)
    if not cam:
        return None
    return {
        "camera_id": cam.get("camera_id"),
        "intersection_id": cam.get("intersection_id"),
        "road_name": cam.get("primary_road") or cam.get("intersection_name"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="下发视频推理请求命令（P8/对齐 step2）。")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap（默认 ANP_BOOTSTRAP/localhost:9092）")
    ap.add_argument("--source-id", type=int, default=None,
                    help="真身稳定键：从本地目录解析真实 camera_id/intersection_id/road_name（对齐 step2，回流事件挂正确相机）")
    ap.add_argument("--camera-id", default=None, help="目标摄像头标识（覆盖 --source-id 解析值）")
    ap.add_argument("--road-name", default=None, help="路段名（按路段提问的依据；覆盖 --source-id 解析值）")
    ap.add_argument("--intersection-id", default=None, help="路口标识（覆盖 --source-id 解析值）")
    ap.add_argument("--road-segment", default=None, help="路段细分（可选）")
    ap.add_argument("--time-from", default=None, help="推理时间窗起（ISO8601 UTC，可选）")
    ap.add_argument("--time-to", default=None, help="推理时间窗止（ISO8601 UTC，可选）")
    ap.add_argument("--prompt", default="该路段最近有没有事故、拥堵或违章？", help="给视频模型的提问")
    ap.add_argument("--clip-ref", default=None, help="视频片段指针（只传指针不传视频，可选）")
    ap.add_argument("--target-agent-id", default=VISIONHUB_AGENT_ID, help="远端 vision hub 推理体 ID")
    ap.add_argument("--expires-sec", type=float, default=300.0, help="命令过期秒数")
    args = ap.parse_args()

    # --source-id：从目录解析真身标识；显式 --camera-id/--road-name/--intersection-id 优先覆盖。
    resolved = {}
    if args.source_id is not None:
        resolved = _resolve_source(args.source_id) or {}
        if not resolved:
            print(f"[video-command] ⚠️ 目录无 source_id={args.source_id}（先跑 sync_visionhub_cameras.py 同步目录）")
            return 2
    camera_id = args.camera_id or resolved.get("camera_id") or "cam-minzu-east-001"
    road_name = args.road_name or resolved.get("road_name") or "民族大道"
    intersection_id = args.intersection_id or resolved.get("intersection_id")

    time_window = None
    if args.time_from or args.time_to:
        time_window = {"time_from": args.time_from, "time_to": args.time_to}
    params = {
        "camera_id": camera_id,
        "road_name": road_name,
        "intersection_id": intersection_id,
        "road_segment": args.road_segment,
        "time_window": time_window,
        "prompt": args.prompt,
        "clip_ref": args.clip_ref,
    }
    command_id = new_message_id()
    env = command_envelope(
        source=Source(system=SourceSystem.PLATFORM, agent_id=VIDEO_TASK_AGENT_ID),
        target_agent_id=args.target_agent_id,
        payload=CommandPayload(
            command_id=command_id, command_type=CommandType.REQUEST_VIDEO_TEXT, params=params
        ),
        expires_at=expires_at_iso(args.expires_sec),
        object_id=intersection_id or road_name or camera_id,
    )

    producer = make_producer(bootstrap_servers=args.bootstrap)
    try:
        publish(producer, VideoTopics.COMMAND, env, flush=True)
    finally:
        producer.close()
    print(f"[video-command] 已发布 request_video_text → {VideoTopics.COMMAND}")
    print(f"[video-command]   command_id={command_id}")
    if args.source_id is not None:
        print(f"[video-command]   source_id={args.source_id} → 真身对齐标识")
    print(f"[video-command]   target={args.target_agent_id} camera={camera_id} road={road_name} inter={intersection_id}")
    print(f"[video-command]   prompt={args.prompt!r}")
    print("[video-command] 关联键 = command_id；回流文本经 result-bridge 入库后即可问答。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
