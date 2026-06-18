#!/usr/bin/env python3
"""端到端冒烟：虚拟感知体 → Kafka → 系统级智能体 → 状态层 topic → 取回校验。

全链路真实经过 Kafka 两个 topic（观测 + 状态）。用受控 event_ts 批量产观测，
覆盖多个 10s 窗口；用显式分区 assign + seek_to_end 建基线，规避历史消息与 offset
竞态，因此可重复运行。

前置：Kafka 已起、topic 已建（deploy/README.md）。退出码 0 = 通过。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_world_status.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kafka import TopicPartition  # noqa: E402

from agents.virtual_traffic import VirtualTrafficAgent  # noqa: E402
from anp.contracts import (  # noqa: E402
    Envelope,
    IntersectionStatusPayload,
    TrafficTopics,
    iso_utc,
    parse_payload,
)
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402
from anp.system_agent import SystemAgent  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
INTERSECTION = "gg-xiongchu-minzu"
N_SAMPLES = 12          # 每 2s 一拍 → 覆盖 [0,10)/[10,20)/[20,30) 三个窗口
INTERVAL_SEC = 2
EXPECTED_WINDOWS = 3


def _assign_from_end(topic: str, group_suffix: str):
    """订阅 topic 全分区并 seek_to_end，返回 (consumer, 基线 offsets)。"""

    consumer = make_consumer(
        [],
        group_id=f"anp-smoke-{group_suffix}",
        bootstrap_servers=BOOTSTRAP,
        consumer_timeout_ms=8000,
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[smoke] FAIL: topic 无分区（未建？）: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    # seek_to_end 是惰性的：末尾 offset 在下次 poll 才解析。这里用 position() 立刻
    # 钉死当前末尾，确保基线落在「产消息之前」，否则会跳过本次新产的消息。
    for tp in tps:
        consumer.position(tp)
    return consumer, tps


def main() -> int:
    print(f"[smoke] bootstrap={BOOTSTRAP} intersection={INTERSECTION} samples={N_SAMPLES}")

    # 基线：先把两个 topic 的消费位点移到末尾，只读本次新增。
    obs_consumer, _ = _assign_from_end(TrafficTopics.OBSERVATION, "obs")
    status_consumer, _ = _assign_from_end(TrafficTopics.STATUS_INTERSECTION, "status")

    # 1) 产受控观测：event_ts 对齐窗口边界，base = floor(now/10)*10。
    producer = make_producer(bootstrap_servers=BOOTSTRAP)
    agent_src = VirtualTrafficAgent(intersection_id=INTERSECTION, seed=7)
    base_epoch = (int(datetime.now(timezone.utc).timestamp()) // 10) * 10
    base = datetime.fromtimestamp(base_epoch, tz=timezone.utc)
    sent_ts: list[str] = []
    for i in range(N_SAMPLES):
        ts = iso_utc(base + timedelta(seconds=i * INTERVAL_SEC))
        agent_src.publish_once(producer, event_ts=ts)
        sent_ts.append(ts)
    producer.flush()
    print(f"[smoke] produced {N_SAMPLES} observations event_ts[{sent_ts[0]} .. {sent_ts[-1]}]")

    # 2) 系统级智能体消费观测、产出 World Status。
    sys_producer = make_producer(bootstrap_servers=BOOTSTRAP)
    sys_agent = SystemAgent(producer=sys_producer)
    consumed = 0
    while consumed < N_SAMPLES:
        batch = obs_consumer.poll(timeout_ms=4000, max_records=64)
        if not batch:
            break
        for records in batch.values():
            for rec in records:
                env = Envelope.model_validate(rec.value)
                if env.scope.object_id != INTERSECTION:
                    continue  # 只关心本次冒烟的路口
                sys_agent.feed_envelope(env)
                consumed += 1
    sys_agent.flush()           # 结算最后一个未达水位的窗口
    sys_producer.flush()
    obs_consumer.close()
    print(
        f"[smoke] system-agent consumed={consumed} windows_emitted={sys_agent.windows_emitted} "
        f"dropped_late={sys_agent.aggregator.dropped_late}"
    )

    if sys_agent.windows_emitted != EXPECTED_WINDOWS:
        print(f"[smoke] FAIL: 期望结算 {EXPECTED_WINDOWS} 个窗口，实际 {sys_agent.windows_emitted}")
        return 1

    # 3) 从状态层 topic 取回本次产出的 World Status 并校验。
    fetched: list[IntersectionStatusPayload] = []
    while len(fetched) < EXPECTED_WINDOWS:
        batch = status_consumer.poll(timeout_ms=4000, max_records=64)
        if not batch:
            break
        for records in batch.values():
            for rec in records:
                env = Envelope.model_validate(rec.value)
                if env.scope.object_id != INTERSECTION:
                    continue
                payload = parse_payload(env)
                assert isinstance(payload, IntersectionStatusPayload)
                fetched.append(payload)
    status_consumer.close()

    if len(fetched) < EXPECTED_WINDOWS:
        print(f"[smoke] FAIL: 状态层只取回 {len(fetched)}/{EXPECTED_WINDOWS} 条 World Status")
        return 1

    # 4) 内容校验：内存当前态 == 最后一个窗口；字段合法。
    for st in fetched:
        ok = (
            st.intersection_id == INTERSECTION
            and st.window.size_sec == 10
            and st.window.sample_count > 0
            and len(st.approaches) >= 1
            and 0.0 <= st.congestion_index <= 1.0
        )
        if not ok:
            print(f"[smoke] FAIL: World Status 字段非法: {st.model_dump()}")
            return 1
        print(
            f"[smoke]   window[{st.window.start}..{st.window.end}] samples={st.window.sample_count} "
            f"queue={st.queue_length_m:.1f}m flow={st.flow_veh_h:.0f}veh/h "
            f"speed={st.mean_speed_kmh:.1f}km/h delay={st.mean_delay_sec:.1f}s -> {st.congestion_level.value}"
        )

    latest = sys_agent.store.get(INTERSECTION)
    if latest is None or latest.window.end != fetched[-1].window.end:
        print("[smoke] FAIL: 内存当前态与最后一个窗口不一致")
        return 1

    print(f"[smoke] PASS: 端到端 World Status 链路一致（结算 {len(fetched)} 窗口，当前态路口 {len(sys_agent.store)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
