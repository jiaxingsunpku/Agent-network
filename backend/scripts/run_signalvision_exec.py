#!/usr/bin/env python3
"""运行 SignalVision 信号控制**执行体**（P6，接真实 SV Dashboard）。

订阅控制层 `anp.traffic.command.v1` → 去重/过期/目标匹配/本地 Safety Guard →
调真实 SV `POST /api/junctions/<id>/update` 写 `traffic_light` → 回 ack 到
`anp.traffic.ack.v1`。启动时注册（lifecycle）、周期发心跳（携 SV 可达性，守护线程）、
退出时下线。Ctrl-C 停止。

配合 run_gateway.py + run_signalvision_adapter.py（或 run_virtual_agent.py）可端到端
跑通真实数据源上的命令闭环。命令 `scope.object_id`（intersection_id）经 junction_map
反查目标 SV junction（默认 `gg-xiongchu-minzu` → `intersection_1_1`）。

前置：SV Dashboard 在 --sv-base-url 可达；Kafka 已起、topic 已建。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_signalvision_exec.py \
        --sv-base-url http://127.0.0.1:8080 --junction intersection_1_1 --intersection gg-xiongchu-minzu
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.adapters.signalvision import (  # noqa: E402
    DEFAULT_JUNCTION_MAP,
    SV_EXEC_AGENT_ID,
    SignalVisionExecConfig,
    SignalVisionExecutor,
    exec_heartbeat_envelope,
    exec_lifecycle_envelope,
)
from anp.contracts import Envelope, SequenceGenerator, TrafficTopics  # noqa: E402
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402


def _build_config(args: argparse.Namespace) -> SignalVisionExecConfig:
    if args.junction and args.intersection:
        junction_map = {args.junction: args.intersection}
    else:
        junction_map = dict(DEFAULT_JUNCTION_MAP)
    return SignalVisionExecConfig(
        agent_id=args.agent_id,
        sv_base_url=args.sv_base_url,
        junction_map=junction_map,
        heartbeat_interval_sec=args.heartbeat,
    )


def _heartbeat_loop(producer, executor: SignalVisionExecutor, interval: float, stop: threading.Event) -> None:
    seq = SequenceGenerator()
    while not stop.is_set():
        reachable, err = executor.probe_sv()
        status = "online" if reachable else "degraded"
        publish(
            producer,
            TrafficTopics.AGENT_HEARTBEAT,
            exec_heartbeat_envelope(
                agent_id=executor.config.agent_id, status=status, last_error=err, sequence=seq.next()
            ),
            flush=True,
        )
        stop.wait(interval)


def _executor_loop(
    executor: SignalVisionExecutor, bootstrap: str | None, stop: threading.Event, duration: float | None, rebuild: bool
) -> None:
    if rebuild:
        try:
            n = executor.rebuild_dedup_from_acks(bootstrap=bootstrap)
            print(f"[sv-exec] 去重表重建：装回 {n} 个历史 command_id")
        except Exception as exc:  # noqa: BLE001
            print(f"[sv-exec] 去重表重建跳过（{exc}）")

    producer = make_producer(bootstrap_servers=bootstrap)
    consumer = make_consumer(
        TrafficTopics.COMMAND,
        group_id="anp-sv-exec",
        bootstrap_servers=bootstrap,
        auto_offset_reset="latest",
        consumer_timeout_ms=1000,  # 周期返回以便检查 stop / duration
    )
    deadline = None if duration is None else time.monotonic() + duration
    print(f"[sv-exec] {executor.config.agent_id} 监听命令…（map={executor.config.junction_map}）")
    try:
        while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
            for record in consumer:
                try:
                    env = Envelope.model_validate(record.value)
                except Exception:  # noqa: BLE001
                    executor.dropped_invalid += 1
                    continue
                ack = executor.handle_command(env)
                if ack is not None:
                    executor.publish_ack(producer, ack, target_agent_id=env.source.agent_id)
                    print(f"[sv-exec] cmd={ack.command_id[:8]} -> {ack.status.value}")
                if stop.is_set() or (deadline is not None and time.monotonic() >= deadline):
                    break
    finally:
        consumer.close()
        producer.flush()
        producer.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="SignalVision 信号控制执行体（P6）。")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap（默认 ANP_BOOTSTRAP/localhost:9092）")
    ap.add_argument("--sv-base-url", default="http://127.0.0.1:8080", help="SV Dashboard HTTP API 根地址")
    ap.add_argument("--agent-id", default=SV_EXEC_AGENT_ID)
    ap.add_argument("--junction", default=None, help="SV junction_id（与 --intersection 成对覆盖默认映射）")
    ap.add_argument("--intersection", default=None, help="平台 intersection_id")
    ap.add_argument("--heartbeat", type=float, default=5.0, help="心跳间隔（秒）")
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒），默认永久")
    ap.add_argument("--no-rebuild", action="store_true", help="启动不重放 ack 重建去重表")
    args = ap.parse_args()

    config = _build_config(args)
    executor = SignalVisionExecutor(config)
    print(f"[sv-exec] agent_id={config.agent_id} sv={config.sv_base_url} map={config.junction_map}")

    lifecycle_producer = make_producer(bootstrap_servers=args.bootstrap)
    publish(
        lifecycle_producer,
        TrafficTopics.AGENT_LIFECYCLE,
        exec_lifecycle_envelope(agent_id=config.agent_id, registered=True),
        flush=True,
    )
    print(f"[sv-exec] 已注册 {config.agent_id}（exec, command_types=[set_signal_plan]）")

    stop = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(lifecycle_producer, executor, config.heartbeat_interval_sec, stop),
        name="sv-exec-heartbeat",
        daemon=True,
    )
    hb_thread.start()

    try:
        _executor_loop(executor, args.bootstrap, stop, args.duration, rebuild=not args.no_rebuild)
        print(
            f"[sv-exec] 退出。processed={executor.processed} rejected={executor.rejected} "
            f"failed={executor.failed} ignored={executor.ignored} invalid={executor.dropped_invalid}"
        )
    except KeyboardInterrupt:
        print("\n[sv-exec] 收到中断，停止执行。")
    finally:
        stop.set()
        time.sleep(0.2)
        try:
            publish(
                lifecycle_producer,
                TrafficTopics.AGENT_LIFECYCLE,
                exec_lifecycle_envelope(agent_id=config.agent_id, registered=False),
                flush=True,
            )
            print(f"[sv-exec] 已下线 {config.agent_id}")
        finally:
            lifecycle_producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
