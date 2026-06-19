#!/usr/bin/env python3
"""回放样例视频文本事件到 Kafka（topic ``anp.video.perception.text.v1``）。

模拟「视频智能体（感知体）把视频大模型处理后的文本事件发到 ANP」。原始视频不进 Kafka。
配合 ``run_video_ingest.py`` 入库、网关/``run_video_qa.py`` 问答。

前置：Kafka 已起、topic 已建（deploy/README.md → 需含 anp.video.perception.text.v1）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/replay_video_text.py
    replay_video_text.py --bootstrap localhost:9092 --file backend/tests/data/video_text_events.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.contracts import VideoTopics  # noqa: E402
from anp.messaging import make_producer, publish  # noqa: E402
from anp.video.config import VIDEO_PERCEPTION_AGENT_ID  # noqa: E402
from anp.video.models import VideoTextEventIn  # noqa: E402

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "tests" / "data" / "video_text_events.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="回放视频文本事件到 Kafka")
    ap.add_argument("--bootstrap", default=None, help="缺省取 ANP_BOOTSTRAP/localhost:9092")
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="样例事件 JSON（数组）")
    ap.add_argument("--agent-id", default=VIDEO_PERCEPTION_AGENT_ID)
    args = ap.parse_args()

    events = json.loads(Path(args.file).read_text(encoding="utf-8"))
    producer = make_producer(bootstrap_servers=args.bootstrap)
    n = 0
    try:
        for ev in events:
            env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id=args.agent_id)
            publish(producer, VideoTopics.PERCEPTION_TEXT, env)
            n += 1
        producer.flush()
    finally:
        producer.close()
    print(f"[replay] 已发布 {n} 条视频文本事件 → {VideoTopics.PERCEPTION_TEXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
