#!/usr/bin/env python3
"""最小冒烟：用 contracts builder 发一条 observation 到 Kafka，再精确取回校验往返。

前置：Kafka 已起、topic 已建（见 deploy/README.md）。

用法::

    backend/.venv/bin/python backend/scripts/smoke_roundtrip.py
    ANP_BOOTSTRAP=localhost:9092 backend/.venv/bin/python backend/scripts/smoke_roundtrip.py

退出码 0 = 往返成功；非 0 = 失败。
"""

from __future__ import annotations

import json
import os
import sys

from kafka import KafkaConsumer, KafkaProducer, TopicPartition

from anp.contracts import (
    Approach,
    Direction,
    Envelope,
    ObservationPayload,
    TrafficTopics,
    observation_envelope,
    parse_payload,
    partition_key,
)

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
TOPIC = TrafficTopics.OBSERVATION
AGENT_ID = "traffic-virtual-001"


def build_message() -> Envelope:
    payload = ObservationPayload(
        intersection_id="gg-xiongchu-minzu",
        approaches=[
            Approach(direction=Direction.NORTH, vehicle_count=12, halting_count=5, mean_speed_mps=8.3),
            Approach(direction=Direction.SOUTH, vehicle_count=7, halting_count=2, mean_speed_mps=11.0),
        ],
    )
    return observation_envelope(agent_id=AGENT_ID, payload=payload, sequence=0)


def main() -> int:
    env = build_message()
    key = partition_key(env)
    print(f"[smoke] bootstrap={BOOTSTRAP} topic={TOPIC} key={key} message_id={env.message_id}")

    # ---- 生产 ----
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks="all",
        retries=3,
    )
    try:
        meta = producer.send(TOPIC, key=key, value=env.to_wire()).get(timeout=15)
        producer.flush()
    finally:
        producer.close()
    print(f"[smoke] produced -> partition={meta.partition} offset={meta.offset}")

    # ---- 精确消费回我们刚写的那条 ----
    consumer = KafkaConsumer(
        bootstrap_servers=BOOTSTRAP,
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        key_deserializer=lambda b: b.decode("utf-8") if b is not None else None,
        consumer_timeout_ms=15000,
    )
    try:
        tp = TopicPartition(TOPIC, meta.partition)
        consumer.assign([tp])
        consumer.seek(tp, meta.offset)
        records = consumer.poll(timeout_ms=15000, max_records=10)
    finally:
        consumer.close()

    fetched = [r for rs in records.values() for r in rs if r.offset == meta.offset]
    if not fetched:
        print("[smoke] FAIL: 未在目标 offset 取回消息")
        return 1
    record = fetched[0]

    # ---- 用契约模型校验往返 ----
    back = Envelope.model_validate(record.value)
    payload = parse_payload(back)
    ok = (
        record.key == key
        and back.message_id == env.message_id
        and isinstance(payload, ObservationPayload)
        and payload.intersection_id == "gg-xiongchu-minzu"
        and len(payload.approaches) == 2
    )
    if not ok:
        print("[smoke] FAIL: 往返内容不一致")
        print("  key:", record.key, "message_id:", back.message_id)
        return 1

    print(
        f"[smoke] consumed key={record.key} message_id={back.message_id} "
        f"intersection={payload.intersection_id} approaches={len(payload.approaches)}"
    )
    print("[smoke] PASS: envelope Kafka 往返一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
