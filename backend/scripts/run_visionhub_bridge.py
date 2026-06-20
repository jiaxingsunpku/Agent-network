#!/usr/bin/env python3
"""运行 ANP↔vision hub 双向桥（P8）：命令桥 + 结果桥 + 共享对账表。

两条独立消费线程（共享一个 :class:`CommandTracker`）：

- **命令桥**：消费 ANP ``anp.video.command.v1`` → 译 → 发 vision hub ``visionhub.world_model.info.v1``。
- **结果桥**：消费 vision hub ``edge.observation.result.v1`` → 译 → 发 ANP ``anp.video.perception.text.v1``
  （P7 ingest 入库）。

是 ANP↔vision hub 的唯一翻译边界。step1：``--visionhub-bootstrap`` 与 ANP 同本地 broker；
step2：覆盖为 wangxuan 可达地址（见 phases/P8.md）。Ctrl-C 停。

前置：Kafka 已起、topic 已建（含 anp.video.command.v1）。配合 ``stub_visionhub_agent.py``（step1）
或真实 vision hub（step2）、``run_video_ingest.py``/网关（入库问答）端到端。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_visionhub_bridge.py
    run_visionhub_bridge.py --visionhub-bootstrap <wangxuan-broker> --from-beginning
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.adapters.visionhub import (  # noqa: E402
    CommandTracker,
    VisionHubBridgeConfig,
    VisionHubCommandBridge,
    VisionHubResultBridge,
    ensure_visionhub_topics,
)
from anp.contracts import Envelope, VideoTopics  # noqa: E402
from anp.messaging import make_consumer, make_producer  # noqa: E402


def _command_loop(bridge: VisionHubCommandBridge, anp_bootstrap, stop, from_beginning, deadline) -> None:
    """ANP 命令 → vision hub info。"""

    consumer = make_consumer(
        VideoTopics.COMMAND,
        group_id="anp-visionhub-command-bridge",
        bootstrap_servers=anp_bootstrap,
        auto_offset_reset="earliest" if from_beginning else "latest",
        consumer_timeout_ms=1000,
    )
    producer = make_producer(bootstrap_servers=bridge.config.visionhub_bootstrap)
    print(f"[vh-bridge] 命令桥：{VideoTopics.COMMAND} → {bridge.config.info_topic}")
    try:
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            for record in consumer:
                try:
                    env = Envelope.model_validate(record.value)
                except Exception:  # noqa: BLE001
                    bridge.skipped += 1
                    continue
                info = bridge.handle(env, producer)
                if info is not None:
                    producer.flush()
                    print(f"[vh-bridge] → 下发 cmd={info['payload']['command_id'][:8]} camera={info['payload'].get('camera_id')}")
                if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
                    break
    finally:
        consumer.close()
        producer.flush()
        producer.close()


def _result_loop(bridge: VisionHubResultBridge, anp_bootstrap, stop, from_beginning, deadline) -> None:
    """vision hub 结果 → ANP 视频感知层文本事件。"""

    consumer = make_consumer(
        bridge.config.result_topic,
        group_id="anp-visionhub-result-bridge",
        bootstrap_servers=bridge.config.visionhub_bootstrap,
        auto_offset_reset="earliest" if from_beginning else "latest",
        consumer_timeout_ms=1000,
    )
    producer = make_producer(bootstrap_servers=anp_bootstrap)
    print(f"[vh-bridge] 结果桥：{bridge.config.result_topic} → {VideoTopics.PERCEPTION_TEXT}")
    try:
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            for record in consumer:
                env = bridge.handle(record.value, producer)
                if env is not None:
                    producer.flush()
                    cid = env.trace.parent_trace_id
                    print(f"[vh-bridge] ← 回流 cmd={(cid or '?')[:8]} → 入库 event={env.message_id[:8]}")
                if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
                    break
    finally:
        consumer.close()
        producer.flush()
        producer.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ANP↔vision hub 双向桥（P8）。")
    ap.add_argument("--bootstrap", default=None, help="ANP Kafka bootstrap（默认 ANP_BOOTSTRAP/localhost:9092）")
    ap.add_argument("--visionhub-bootstrap", default=None, help="vision hub Kafka bootstrap（step1 默认同 ANP）")
    ap.add_argument("--from-beginning", action="store_true", help="两侧均从最早重放（默认只收新消息）")
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒），默认永久")
    args = ap.parse_args()

    config = VisionHubBridgeConfig(visionhub_bootstrap=args.visionhub_bootstrap)
    if args.visionhub_bootstrap is None:  # step1 本地：确保 vision hub 外部 topic 存在
        created = ensure_visionhub_topics(config, bootstrap=args.bootstrap)
        if created:
            print(f"[vh-bridge] 已创建 vision hub 外部 topic（step1 本地）：{created}")
    tracker = CommandTracker()
    cmd_bridge = VisionHubCommandBridge(config, tracker=tracker)
    res_bridge = VisionHubResultBridge(config, tracker=tracker)
    print(f"[vh-bridge] ANP={args.bootstrap or 'localhost:9092'} visionhub={args.visionhub_bootstrap or '(同 ANP)'}")

    stop = threading.Event()
    deadline = None if args.duration is None else time.monotonic() + args.duration
    threads = [
        threading.Thread(target=_command_loop, args=(cmd_bridge, args.bootstrap, stop, args.from_beginning, deadline), name="vh-command", daemon=True),
        threading.Thread(target=_result_loop, args=(res_bridge, args.bootstrap, stop, args.from_beginning, deadline), name="vh-result", daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
            if deadline is not None and time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        print("\n[vh-bridge] 收到中断，停止双向桥。")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
    print(
        f"[vh-bridge] 退出。命令桥 forwarded={cmd_bridge.forwarded} skipped={cmd_bridge.skipped}；"
        f"结果桥 republished={res_bridge.republished} skipped={res_bridge.skipped}；"
        f"对账 dispatched={tracker.dispatched} returned={tracker.returned_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
