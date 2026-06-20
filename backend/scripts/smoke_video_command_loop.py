#!/usr/bin/env python3
"""端到端冒烟（P8 step1）：ANP 命令 → 命令桥 → vision hub 替身（桩推理）→ 结果桥 → P7 入库 → 问答。

全链路真实经过 4 个 Kafka topic（本机两程序模型，单 broker）：

    anp.video.command.v1 ─[命令桥]→ visionhub.world_model.info.v1 ─[替身桩推理]→
    edge.observation.result.v1 ─[结果桥]→ anp.video.perception.text.v1 ─[P7 ingest]→ 文本库 → 问答

断言：命令桥转发 request_video_text、跳过非视频命令；替身产文本；结果桥译回入库；CommandTracker
经 correlation_id 关联「已发→收到结果」；问答查到这条**新产生**的结果，且 event_id 在三处一致
（tracker.returned_event_id == 入库 event == 问答证据 event）。退出码 0 = 通过。

前置：Kafka 已起、topic 已建（deploy/create_topics.sh，需含 anp.video.command.v1）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_video_command_loop.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 允许 import 同目录替身脚本

from kafka import TopicPartition  # noqa: E402

from anp.adapters.visionhub import (  # noqa: E402
    VISIONHUB_AGENT_ID,
    CommandTracker,
    VisionHubBridgeConfig,
    VisionHubCommandBridge,
    VisionHubResultBridge,
    ensure_visionhub_topics,
)
from anp.adapters.visionhub.config import VISIONHUB_INFO_TOPIC, VISIONHUB_RESULT_TOPIC  # noqa: E402
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
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402
from anp.video.config import VIDEO_TASK_AGENT_ID, LLMConfig  # noqa: E402
from anp.video.ingest import ingest_value  # noqa: E402
from anp.video.qa import VideoQAService  # noqa: E402
from anp.video.retrieval import SearchFilters  # noqa: E402
from anp.video.store import SqliteVideoTextStore  # noqa: E402
from stub_visionhub_agent import handle_info  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
ROAD = "民族大道"
CAMERA = "cam-minzu-east-001"
_NO_LLM = LLMConfig(base_url=None, model="x", api_key=None, proxy=None)


def _assign_from_end(topic: str, suffix: str):
    consumer = make_consumer(
        [], group_id=f"anp-vhcmd-smoke-{suffix}", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[vhcmd-smoke] FAIL: topic 无分区（未建？）: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def _video_command(command_id: str, *, prompt: str, command_type=CommandType.REQUEST_VIDEO_TEXT):
    params = {"camera_id": CAMERA, "road_name": ROAD, "prompt": prompt}
    if command_type != CommandType.REQUEST_VIDEO_TEXT:
        params = {"desired_phase": "north_south_green", "duration_s": 25}  # 非视频命令的合法 params
    return command_envelope(
        source=Source(system=SourceSystem.PLATFORM, agent_id=VIDEO_TASK_AGENT_ID),
        target_agent_id=VISIONHUB_AGENT_ID,
        payload=CommandPayload(command_id=command_id, command_type=command_type, params=params),
        expires_at=expires_at_iso(300.0),
        object_id=ROAD,
    )


def main() -> int:
    print(f"[vhcmd-smoke] bootstrap={BOOTSTRAP}")
    print(f"[vhcmd-smoke] 链路 {VideoTopics.COMMAND} → {VISIONHUB_INFO_TOPIC} → {VISIONHUB_RESULT_TOPIC} → {VideoTopics.PERCEPTION_TEXT}")
    config = VisionHubBridgeConfig(visionhub_bootstrap=BOOTSTRAP)  # step1：vision hub 用同一本地 broker
    tracker = CommandTracker()
    failures: list[str] = []

    # step1 便利：确保 vision hub 外部 topic 存在（本机 broker 关了 auto-create）。
    created = ensure_visionhub_topics(config, bootstrap=BOOTSTRAP)
    if created:
        print(f"[vhcmd-smoke] 已创建 vision hub 外部 topic（step1 本地）：{created}")

    # 建 4 个基线消费者（assign + seek_to_end，仅消费本次新增）。
    c_cmd = _assign_from_end(VideoTopics.COMMAND, "cmd")
    c_info = _assign_from_end(VISIONHUB_INFO_TOPIC, "info")
    c_result = _assign_from_end(VISIONHUB_RESULT_TOPIC, "result")
    c_text = _assign_from_end(VideoTopics.PERCEPTION_TEXT, "text")

    # 1) 发命令：1 条 request_video_text（含「事故」）+ 1 条 set_signal_plan（非视频，应被命令桥跳过）。
    cid = new_message_id()
    producer = make_producer(bootstrap_servers=BOOTSTRAP)
    publish(producer, VideoTopics.COMMAND, _video_command(cid, prompt=f"{ROAD}最近有没有事故？"))
    publish(producer, VideoTopics.COMMAND, _video_command("sig-skip-001", prompt="", command_type=CommandType.SET_SIGNAL_PLAN), flush=True)
    producer.close()
    print(f"[vhcmd-smoke] 已发命令 command_id={cid[:8]}（+1 条非视频命令验证跳过）")

    # 2) 命令桥：ANP 命令 → vision hub info。
    p_info = make_producer(bootstrap_servers=config.visionhub_bootstrap)
    cmd_bridge = VisionHubCommandBridge(config, tracker=tracker)
    cmd_bridge.run(c_cmd, p_info)
    c_cmd.close()
    p_info.close()
    print(f"[vhcmd-smoke] 命令桥 forwarded={cmd_bridge.forwarded} skipped={cmd_bridge.skipped} dispatched={tracker.dispatched}")
    if cmd_bridge.forwarded != 1:
        failures.append(f"命令桥应转发 1 条 request_video_text，实际 {cmd_bridge.forwarded}")
    if cmd_bridge.skipped != 1:
        failures.append(f"命令桥应跳过 1 条非视频命令，实际 skipped={cmd_bridge.skipped}")

    # 3) vision hub 替身：info → 桩推理 → result。
    p_result = make_producer(bootstrap_servers=config.visionhub_bootstrap)
    stub_produced = 0
    for record in c_info:
        if handle_info(record.value, p_result, config.result_topic) is not None:
            stub_produced += 1
    c_info.close()
    p_result.flush()
    p_result.close()
    print(f"[vhcmd-smoke] 替身桩推理 produced={stub_produced}")
    if stub_produced != 1:
        failures.append(f"替身应产 1 条文本结果，实际 {stub_produced}")

    # 4) 结果桥：vision hub result → ANP 视频感知层文本事件。
    p_text = make_producer(bootstrap_servers=BOOTSTRAP)
    res_bridge = VisionHubResultBridge(config, tracker=tracker)
    res_bridge.run(c_result, p_text)
    c_result.close()
    p_text.close()
    print(f"[vhcmd-smoke] 结果桥 republished={res_bridge.republished} returned={tracker.returned_count}")
    if res_bridge.republished != 1:
        failures.append(f"结果桥应回流 1 条，实际 {res_bridge.republished}")

    rec = tracker.get(cid)
    if rec is None or not rec.returned:
        failures.append(f"对账：command_id={cid[:8]} 未标记『已收到结果』: {rec}")
    returned_event_id = rec.returned_event_id if rec else None

    # 5) P7 ingest 入库。
    tmpdir = tempfile.mkdtemp(prefix="anp-vhcmd-smoke-")
    store = SqliteVideoTextStore(Path(tmpdir) / "v.db")
    ingested_ids: list[str] = []
    for record in c_text:
        if ingest_value(store, record.value) == "new":
            env = record.value
            ingested_ids.append(env.get("message_id"))
    c_text.close()
    print(f"[vhcmd-smoke] 入库 new={len(ingested_ids)} store_count={store.count()}")
    if len(ingested_ids) != 1:
        failures.append(f"应入库 1 条新事件，实际 {len(ingested_ids)}")

    # 三处 event_id 一致性：tracker.returned_event_id == 入库 event。
    if returned_event_id and ingested_ids and returned_event_id != ingested_ids[0]:
        failures.append(f"event_id 不一致：tracker={returned_event_id} 入库={ingested_ids[0]}")

    # 6) 问答查到这条新结果（规则摘要，确定性）。
    qa = VideoQAService(store, llm_config=_NO_LLM)
    res = qa.answer(f"{ROAD}最近有什么情况？", base_filters=SearchFilters(road_name=ROAD))
    ev_ids = [e.get("event_id") for e in res["evidence"]]
    print(f"[vhcmd-smoke] 问答命中 {len(res['evidence'])} 条；answer: {res['answer'][:60]}…")
    if not res["evidence"]:
        failures.append("问答应召回新产生的视频文本结果")
    elif returned_event_id and returned_event_id not in ev_ids:
        failures.append(f"问答证据未含回流事件 {returned_event_id}（实际 {ev_ids}）")
    else:
        cat = res["evidence"][0].get("category")
        print(f"[vhcmd-smoke]   证据类别={cat} event={ev_ids[0][:8]}（与 tracker/入库一致）")

    if failures:
        for f in failures:
            print(f"[vhcmd-smoke] FAIL: {f}")
        return 1
    print("[vhcmd-smoke] PASS: 命令→桩推理→结果→译→入库→问答 双向闭环一致（correlation_id 关联，event_id 三处一致）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
