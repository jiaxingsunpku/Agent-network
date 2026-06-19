#!/usr/bin/env python3
"""端到端冒烟：样例视频文本事件 → Kafka → ingest 入库 → 检索问答（answer + evidence）。

链路全真实经过 Kafka（topic ``anp.video.perception.text.v1``）：
1. 发布样例事件（含一条重复 message_id 验证幂等去重）；
2. ingest 把消息写入临时 SQLite 文本库；
3. 检索问答：按时间/路段提问命中事故；无关问题返回空。
核心断言走**规则摘要**（确定性、不出网）；末尾对 GLM 做 best-effort 探针（失败仅告警）。
退出码 0 = 通过。

前置：Kafka 已起、topic 已建（deploy/create_topics.sh，需含 anp.video.* ）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_video_qa.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kafka import TopicPartition  # noqa: E402

from anp.contracts import VideoTopics  # noqa: E402
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402
from anp.video.config import VIDEO_PERCEPTION_AGENT_ID, LLMConfig  # noqa: E402
from anp.video.ingest import ingest_value  # noqa: E402
from anp.video.models import VideoTextEventIn  # noqa: E402
from anp.video.qa import VideoQAService  # noqa: E402
from anp.video.retrieval import SearchFilters  # noqa: E402
from anp.video.store import SqliteVideoTextStore  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
DATA = Path(__file__).resolve().parents[1] / "tests" / "data" / "video_text_events.json"
_NO_LLM = LLMConfig(base_url=None, model="x", api_key=None, proxy=None)


def _assign_from_end(topic: str):
    consumer = make_consumer(
        [], group_id="anp-video-smoke", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[video-smoke] FAIL: topic 无分区（未建？）: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def main() -> int:
    print(f"[video-smoke] bootstrap={BOOTSTRAP} topic={VideoTopics.PERCEPTION_TEXT}")
    events = json.loads(DATA.read_text(encoding="utf-8"))
    n_events = len(events)

    consumer = _assign_from_end(VideoTopics.PERCEPTION_TEXT)

    # 1) 发布样例事件 + 一条重复（同 message_id）验证幂等。
    producer = make_producer(bootstrap_servers=BOOTSTRAP)
    first_mid = None
    for i, ev in enumerate(events):
        env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id=VIDEO_PERCEPTION_AGENT_ID)
        if i == 0:
            first_mid = env.message_id
        publish(producer, VideoTopics.PERCEPTION_TEXT, env)
    # 重复投递第一条（构造同 message_id）
    dup_env = VideoTextEventIn.model_validate(events[0]).to_envelope(
        default_agent_id=VIDEO_PERCEPTION_AGENT_ID
    )
    dup_env = dup_env.model_copy(update={"message_id": first_mid})
    publish(producer, VideoTopics.PERCEPTION_TEXT, dup_env, flush=True)
    producer.close()
    print(f"[video-smoke] published {n_events} events + 1 duplicate")

    # 2) ingest 入库（临时 SQLite）。
    tmpdir = tempfile.mkdtemp(prefix="anp-video-smoke-")
    store = SqliteVideoTextStore(Path(tmpdir) / "v.db")
    new = dup = skip = 0
    expected = n_events + 1
    seen = 0
    while seen < expected:
        batch = consumer.poll(timeout_ms=4000, max_records=64)
        if not batch:
            break
        for records in batch.values():
            for rec in records:
                outcome = ingest_value(store, rec.value)
                seen += 1
                new += outcome == "new"
                dup += outcome == "duplicate"
                skip += outcome == "skipped"
    consumer.close()
    print(f"[video-smoke] ingest new={new} dup={dup} skipped={skip} store_count={store.count()}")
    if new != n_events or dup < 1:
        print(f"[video-smoke] FAIL: 期望新写入 {n_events} 条 + 至少 1 条去重")
        return 1

    # 3) 检索问答（规则摘要，确定性）。
    qa = VideoQAService(store, llm_config=_NO_LLM)
    res = qa.answer(
        "6月13号下午民族大道有没有事故？",
        base_filters=SearchFilters(
            time_from="2026-06-13T04:00:00Z", time_to="2026-06-13T09:00:00Z", road_name="民族大道"
        ),
    )
    has_accident = any(e.get("category") == "事故" for e in res["evidence"])
    print(f"[video-smoke] Q1 命中 {len(res['evidence'])} 条，含事故={has_accident}")
    print(f"[video-smoke]   answer: {res['answer'][:80]}…")
    if not res["evidence"] or not has_accident:
        print("[video-smoke] FAIL: 应召回民族大道下午的事故事件")
        return 1

    res2 = qa.answer("光谷大道有没有事故", base_filters=SearchFilters(road_name="光谷大道"))
    if res2["evidence"]:
        print("[video-smoke] FAIL: 无关路段不应召回")
        return 1
    print(f"[video-smoke] Q2 无关问题正确返回空（{res2['answer'][:30]}…）")

    # 4) GLM best-effort 探针（不影响 PASS/FAIL）。
    llm = LLMConfig.from_env()
    if llm.enabled:
        try:
            glm_res = VideoQAService(store, llm_config=llm).answer(
                "6月13号下午民族大道发生了什么？",
                base_filters=SearchFilters(
                    time_from="2026-06-13T04:00:00Z", time_to="2026-06-13T09:00:00Z", road_name="民族大道"
                ),
            )
            warn = " | ".join(glm_res["warnings"]) or "无"
            print(f"[video-smoke] GLM 探针 OK（model={llm.model}）warnings={warn}")
            print(f"[video-smoke]   GLM answer: {glm_res['answer'][:120]}…")
        except Exception as exc:  # noqa: BLE001
            print(f"[video-smoke] GLM 探针失败（非阻塞）：{exc}")
    else:
        print("[video-smoke] GLM 未配置，跳过探针（问答走规则摘要）")

    print("[video-smoke] PASS: 视频文本事件 → Kafka → ingest → 检索问答 链路一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
