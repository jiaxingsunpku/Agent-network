#!/usr/bin/env python3
"""端到端命令闭环冒烟：网关/命令源 → Kafka command → 虚拟体执行 → Kafka ack。

覆盖 docs/protocol.md §5 的全部分支：
- 合法命令 → completed（含「网关真实发布路径」state.publish_command）；
- 越界参数 → rejected（本地 Safety Guard）；
- 过期命令 → expired；
- 重复 command_id → duplicate；
- 非本体目标 → 忽略（无 ack）。

用 assign + seek_to_end 建基线，仅消费本次新增，可重复运行。退出码 0 = 通过。

前置：Kafka 已起、topic 已建（deploy/README.md）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_commands.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kafka import TopicPartition  # noqa: E402

from agents.virtual_traffic import VIRTUAL_AGENT_ID, VirtualTrafficExecutor  # noqa: E402
from anp.contracts import (  # noqa: E402
    AckPayload,
    CommandPayload,
    CommandType,
    Envelope,
    Source,
    SourceSystem,
    command_envelope,
    expires_at_iso,
    parse_payload,
)
from anp.gateway import GatewayState, PublishUnavailable  # noqa: E402
from anp.gateway.commands import build_command_envelope  # noqa: E402
from anp.gateway.config import GatewayConfig, GATEWAY_AGENT_ID  # noqa: E402
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402
from anp.registry import seed_default_registry  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")


def _assign_from_end(topic: str, suffix: str):
    consumer = make_consumer(
        [], group_id=f"anp-smoke-cmd-{suffix}", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[smoke-cmd] FAIL: topic 无分区: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def _gateway_source() -> Source:
    return Source(system=SourceSystem.PLATFORM, agent_id=GATEWAY_AGENT_ID)


def _manual_command(command_id: str, *, target: str, params: dict, expires_sec: float = 30.0) -> Envelope:
    return command_envelope(
        source=_gateway_source(),
        target_agent_id=target,
        payload=CommandPayload(command_id=command_id, command_type=CommandType.SET_SIGNAL_PLAN, params=params),
        expires_at=expires_at_iso(expires_sec),
        object_id="gg-xiongchu-minzu",
    )


def main() -> int:
    print(f"[smoke-cmd] bootstrap={BOOTSTRAP}")
    cmd_consumer = _assign_from_end("anp.traffic.command.v1", "in")
    ack_consumer = _assign_from_end("anp.traffic.ack.v1", "out")

    producer = make_producer(bootstrap_servers=BOOTSTRAP)
    registry = seed_default_registry()
    state = GatewayState(config=GatewayConfig(with_consumers=False), registry=registry, producer=producer)

    ok_phase = {"desired_phase": "north_south_green", "duration_s": 25}

    # 1) 网关真实发布路径（合法命令）→ 期望 completed。
    env_gw, cid_gw = build_command_envelope(
        {
            "target_agent_id": VIRTUAL_AGENT_ID,
            "command_type": "set_signal_plan",
            "payload": ok_phase,
            "object_id": "gg-xiongchu-minzu",
        },
        registry,
    )
    try:
        state.publish_command(env_gw)
    except PublishUnavailable:
        raise SystemExit("[smoke-cmd] FAIL: 网关 producer 不可用")

    # 2..5) 手工命令（精确控制 command_id 以测去重/过期/越界/异目标）。
    cid_valid = "smoke-valid-001"
    publish(producer, "anp.traffic.command.v1", _manual_command(cid_valid, target=VIRTUAL_AGENT_ID, params=ok_phase))
    publish(
        producer,
        "anp.traffic.command.v1",
        _manual_command("smoke-reject-001", target=VIRTUAL_AGENT_ID, params={"desired_phase": "north_south_green", "duration_s": 999}),
    )
    publish(
        producer,
        "anp.traffic.command.v1",
        _manual_command("smoke-expired-001", target=VIRTUAL_AGENT_ID, params=ok_phase, expires_sec=-5.0),
    )
    # 重复：与 cid_valid 同 id（同分区、在其后），期望 duplicate。
    publish(producer, "anp.traffic.command.v1", _manual_command(cid_valid, target=VIRTUAL_AGENT_ID, params=ok_phase))
    # 异目标：期望被忽略、无 ack。
    publish(
        producer, "anp.traffic.command.v1", _manual_command("smoke-other-001", target="some-other-agent", params=ok_phase)
    )
    producer.flush()
    print("[smoke-cmd] 已发布 6 条命令（含网关路径 1 + 手工 5）")

    # 执行体 drain 消费命令、回 ack。
    executor = VirtualTrafficExecutor(agent_id=VIRTUAL_AGENT_ID)
    ack_producer = make_producer(bootstrap_servers=BOOTSTRAP)
    executor.run(cmd_consumer, ack_producer)
    cmd_consumer.close()
    ack_producer.close()
    print(
        f"[smoke-cmd] executor processed={executor.processed} ignored={executor.ignored} "
        f"invalid={executor.dropped_invalid}"
    )

    # 读取 ack 并按 command_id 归集为「状态列表」（一个 command_id 可能多条 ack，
    # 如 valid 先 completed 再 duplicate）。
    acks: dict[str, list[str]] = {}
    while True:
        batch = ack_consumer.poll(timeout_ms=4000, max_records=64)
        if not batch:
            break
        for records in batch.values():
            for rec in records:
                env = Envelope.model_validate(rec.value)
                payload = parse_payload(env)
                assert isinstance(payload, AckPayload)
                acks.setdefault(payload.command_id, []).append(payload.status.value)
    ack_consumer.close()

    failures = []

    def _expect_contains(cid: str, want: str, note: str = "") -> None:
        got = acks.get(cid, [])
        if want in got:
            print(f"[smoke-cmd]   ok: {cid} -> {want}{note}")
        else:
            failures.append(f"{cid}: 期望含 {want}，实际 {got}")

    _expect_contains(cid_gw, "completed", "（网关真实发布路径）")
    _expect_contains(cid_valid, "completed")
    _expect_contains("smoke-reject-001", "rejected", "（越界参数被 Safety Guard 拒绝）")
    _expect_contains("smoke-expired-001", "expired")
    # 重复：cid_valid 同时应有 completed 与 duplicate 两条。
    _expect_contains(cid_valid, "duplicate", "（重复命令）")

    if "smoke-other-001" in acks:
        failures.append("异目标命令不应产生 ack")
    else:
        print("[smoke-cmd]   ok: 异目标命令被忽略（无 ack）")

    if executor.ignored < 1:
        failures.append("executor.ignored 应 ≥ 1（异目标）")

    if failures:
        for f in failures:
            print(f"[smoke-cmd] FAIL: {f}")
        return 1

    print("[smoke-cmd] PASS: 命令闭环全分支（completed/rejected/expired/duplicate/ignored）一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
