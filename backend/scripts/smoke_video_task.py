#!/usr/bin/env python3
"""端到端冒烟（P9）：协作视频任务 → 扇出定向命令 → 桩回流 → 按 command_id 归因 → QA 聚合。

复用 P8 链路（命令桥/替身桩/结果桥/P7 ingest），但命令由 **编排器** 扇出、回流按 ``command_id``
⇄``parent_trace_id`` 逐命令归因、命中后 QA 聚合落任务状态。两段断言：

- **Part A（N=1 全闭环）**：``create_task`` 扇出 1 条定向命令 → 命令桥 → 替身桩推理 → 结果桥 →
  入库（同一文本库）→ ``refresh_task`` 把命令标 returned、整体 status=aggregated、答案含证据，
  且证据 event 的 ``parent_trace_id`` == 任务命令 ``command_id``（精确归因，非按内容）。
- **Part B（N>1 扇出外形）**：``create_task(target_agent_ids=[a,b])`` 扇出 **2 条** 定向命令，
  消费 ``anp.video.command.v1`` 核验 wire：2 个不同 command_id、各带单一 ``target_agent_id``、
  **无 broadcast/agent_ids 群发字段**（AGENTS §3.5）。

前置：Kafka 已起、topic 已建（含 anp.video.command.v1）。退出码 0 = 通过。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_video_task.py
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
from anp.contracts import VideoTopics  # noqa: E402
from anp.messaging import make_consumer, make_producer  # noqa: E402
from anp.video.config import LLMConfig  # noqa: E402
from anp.video.ingest import ingest_value  # noqa: E402
from anp.video.orchestrator import VideoTaskOrchestrator  # noqa: E402
from anp.video.qa import VideoQAService  # noqa: E402
from anp.video.store import SqliteVideoTextStore  # noqa: E402
from anp.video.tasks import SqliteVideoTaskStore, TaskScope  # noqa: E402
from stub_visionhub_agent import handle_info  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
ROAD = "民族大道"
CAMERA = "cam-minzu-east-001"
_NO_LLM = LLMConfig(base_url=None, model="x", api_key=None, proxy=None)


def _assign_from_end(topic: str, suffix: str):
    consumer = make_consumer(
        [], group_id=f"anp-vtask-smoke-{suffix}", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[vtask-smoke] FAIL: topic 无分区（未建？）: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def main() -> int:
    print(f"[vtask-smoke] bootstrap={BOOTSTRAP}")
    config = VisionHubBridgeConfig(visionhub_bootstrap=BOOTSTRAP)
    tracker = CommandTracker()
    failures: list[str] = []

    created = ensure_visionhub_topics(config, bootstrap=BOOTSTRAP)
    if created:
        print(f"[vtask-smoke] 已创建 vision hub 外部 topic：{created}")

    # 共享一套存储：编排器读的文本库 == ingest 写的文本库。
    tmpdir = tempfile.mkdtemp(prefix="anp-vtask-smoke-")
    text_store = SqliteVideoTextStore(Path(tmpdir) / "video_text.db")
    task_store = SqliteVideoTaskStore(Path(tmpdir) / "video_tasks.db")
    qa = VideoQAService(text_store, llm_config=_NO_LLM)
    producer = make_producer(bootstrap_servers=BOOTSTRAP)
    orch = VideoTaskOrchestrator(task_store=task_store, text_store=text_store, qa=qa, producer=producer)

    # ====== Part A：N=1 全闭环 ======
    c_cmd = _assign_from_end(VideoTopics.COMMAND, "cmd")
    c_info = _assign_from_end(VISIONHUB_INFO_TOPIC, "info")
    c_result = _assign_from_end(VISIONHUB_RESULT_TOPIC, "result")
    c_text = _assign_from_end(VideoTopics.PERCEPTION_TEXT, "text")

    scope = TaskScope(road_name=ROAD, camera_id=CAMERA)
    task = orch.create_task(f"{ROAD}最近有没有事故？", scope)
    cid = task.commands[0].command_id if task.commands else None
    print(f"[vtask-smoke] 任务 {task.task_id[:8]} 扇出 {len(task.commands)} 条命令 status={task.status}")
    if len(task.commands) != 1:
        failures.append(f"N=1 应扇出 1 条命令，实际 {len(task.commands)}")
    if task.commands and task.commands[0].target_agent_id != VISIONHUB_AGENT_ID:
        failures.append(f"命令 target 应为默认 hub，实际 {task.commands[0].target_agent_id}")
    if task.status != "running":
        failures.append(f"刚扇出整体应 running，实际 {task.status}")

    # 命令桥：ANP 命令 → vision hub info。
    p_info = make_producer(bootstrap_servers=config.visionhub_bootstrap)
    cmd_bridge = VisionHubCommandBridge(config, tracker=tracker)
    cmd_bridge.run(c_cmd, p_info)
    c_cmd.close()
    p_info.close()
    if cmd_bridge.forwarded != 1:
        failures.append(f"命令桥应转发 1 条，实际 {cmd_bridge.forwarded}")

    # 替身桩：info → result。
    p_result = make_producer(bootstrap_servers=config.visionhub_bootstrap)
    stub_produced = 0
    for record in c_info:
        if handle_info(record.value, p_result, config.result_topic) is not None:
            stub_produced += 1
    c_info.close()
    p_result.flush()
    p_result.close()
    if stub_produced != 1:
        failures.append(f"替身应产 1 条结果，实际 {stub_produced}")

    # 结果桥：result → ANP 视频感知层文本事件。
    p_text = make_producer(bootstrap_servers=BOOTSTRAP)
    res_bridge = VisionHubResultBridge(config, tracker=tracker)
    res_bridge.run(c_result, p_text)
    c_result.close()
    p_text.close()
    if res_bridge.republished != 1:
        failures.append(f"结果桥应回流 1 条，实际 {res_bridge.republished}")

    # P7 ingest 入到编排器读的同一文本库。
    ingested = 0
    for record in c_text:
        if ingest_value(text_store, record.value) == "new":
            ingested += 1
    c_text.close()
    print(f"[vtask-smoke] 入库 new={ingested} store_count={text_store.count()}")
    if ingested != 1:
        failures.append(f"应入库 1 条回流文本，实际 {ingested}")

    # 刷新任务：归因 + 聚合。
    refreshed = orch.refresh_task(task.task_id, aggregate=True)
    print(f"[vtask-smoke] refresh：status={refreshed.status} returned={refreshed.returned_count} answer={refreshed.answer[:42] if refreshed.answer else None}…")
    if refreshed.status != "aggregated":
        failures.append(f"有回流后整体应 aggregated，实际 {refreshed.status}")
    if refreshed.returned_count != 1:
        failures.append(f"应 1 条命令 returned，实际 {refreshed.returned_count}")
    cmd0 = refreshed.commands[0]
    if cmd0.status != "returned" or not cmd0.returned_event_id:
        failures.append(f"命令应标 returned + returned_event_id，实际 {cmd0}")
    if not refreshed.evidence:
        failures.append("聚合答案应含证据")
    else:
        # 证据 event_id 应 == 命令归因到的回流事件（精确归因，非按内容）。
        ev_ids = [e.get("event_id") for e in refreshed.evidence]
        if cmd0.returned_event_id not in ev_ids:
            failures.append(f"证据未含归因事件 {cmd0.returned_event_id}（实际 {ev_ids}）")
        else:
            print(f"[vtask-smoke]   归因一致：command_id={cid[:8]} ⇄ event={cmd0.returned_event_id[:8]}（证据/命令一致）")

    # ====== Part B：N>1 扇出外形（只验 wire，不跑回流，避免桩同内容去重）======
    c_cmd2 = _assign_from_end(VideoTopics.COMMAND, "cmd2")
    hubs = ["video-visionhub-a-001", "video-visionhub-b-001"]
    task2 = orch.create_task(
        f"{ROAD}多机协同看一下", TaskScope(road_name=ROAD, camera_id=CAMERA, target_agent_ids=hubs)
    )
    if len(task2.commands) != 2:
        failures.append(f"N=2 应扇出 2 条命令，实际 {len(task2.commands)}")
    task2_cids = {c.command_id for c in task2.commands}
    task2_targets = {c.target_agent_id for c in task2.commands}
    if task2_targets != set(hubs):
        failures.append(f"N=2 目标应为 {hubs}，实际 {sorted(task2_targets)}")
    if len(task2_cids) != 2:
        failures.append("N=2 两条命令 command_id 应不同")

    # 消费实际发布到控制层的命令，核验 wire 外形。
    seen_cids: set[str] = set()
    seen_targets: set[str] = set()
    for record in c_cmd2:
        wire = record.value
        if not isinstance(wire, dict) or wire.get("event_type") != "command":
            continue
        payload = wire.get("payload") or {}
        if payload.get("command_id") not in task2_cids:
            continue
        seen_cids.add(payload.get("command_id"))
        tgt = (wire.get("target") or {}).get("agent_id")
        if not tgt:
            failures.append("命令 wire 缺 target.agent_id（疑似群发）")
        seen_targets.add(tgt)
        for key in ("broadcast", "agent_ids"):
            if key in wire or key in (wire.get("target") or {}):
                failures.append(f"命令 wire 出现禁用群发字段 {key}")
    c_cmd2.close()
    if seen_cids != task2_cids:
        failures.append(f"控制层应见 2 条任务命令，实际 {len(seen_cids)}")
    if seen_targets != set(hubs):
        failures.append(f"控制层命令 target 应为 {hubs}，实际 {sorted(seen_targets)}")
    print(f"[vtask-smoke] Part B：扇出 {len(task2.commands)} 条定向命令 targets={sorted(seen_targets)}（无群发字段）")

    producer.close()
    orch.close()

    if failures:
        for f in failures:
            print(f"[vtask-smoke] FAIL: {f}")
        return 1
    print("[vtask-smoke] PASS: 任务扇出定向命令 → 桩回流 → command_id 归因 → QA 聚合；N>1 扇出外形无群发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
