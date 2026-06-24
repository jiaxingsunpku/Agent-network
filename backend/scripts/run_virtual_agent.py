#!/usr/bin/env python3
"""运行 v1 虚拟交通智能体（感知 + 执行二合一）。

默认同时：
- 感知：每 2s 为路口各方向发布合成观测到 anp.traffic.perception.observation.v1（主线程）；
- 执行：消费 anp.traffic.command.v1，跑本地 Safety Guard，回 ack 到 anp.traffic.ack.v1（守护线程）；
- 心跳/注册：启动时注册（lifecycle），周期发心跳（守护线程），退出时下线。

配合 run_system_agent.py + run_gateway.py 可端到端跑通命令闭环。Ctrl-C 停止。
``--no-exec`` 只跑感知（等价旧行为）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_virtual_agent.py
    run_virtual_agent.py --duration 30 --seed 7 --no-exec
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 让 import agents 可用

from agents.virtual_traffic import (  # noqa: E402
    AGENT_CAPABILITIES,
    AGENT_COMMAND_TYPES,
    DEFAULT_HEARTBEAT_SEC,
    DEFAULT_INTERSECTION,
    VIRTUAL_AGENT_ID,
    VirtualTrafficAgent,
    VirtualTrafficExecutor,
)
from anp.contracts import Channel, TrafficTopics  # noqa: E402
from anp.messaging import make_consumer, make_producer  # noqa: E402
from anp.world import WorldClient  # noqa: E402


def _executor_loop(executor: VirtualTrafficExecutor, bootstrap: str | None, stop: threading.Event) -> None:
    # 重启不丢去重：先重放本体既往 ack 重建去重表（protocol.md §6「可重建」），
    # 避免重启后旧 command_id 重投被当新命令重复执行。best-effort，失败不阻断。
    try:
        rebuilt = executor.rebuild_dedup_from_acks(bootstrap=bootstrap)
        print(f"[virtual-exec] 去重表重建：装回 {rebuilt} 个历史 command_id")
    except Exception as exc:  # noqa: BLE001
        print(f"[virtual-exec] 去重表重建跳过（{exc}）")

    producer = make_producer(bootstrap_servers=bootstrap)
    consumer = make_consumer(
        TrafficTopics.COMMAND,
        group_id="anp-virtual-exec",
        bootstrap_servers=bootstrap,
        auto_offset_reset="latest",
        consumer_timeout_ms=1000,  # 周期性返回以便检查 stop
    )
    print(f"[virtual-exec] {executor.agent_id} 监听命令…")
    try:
        while not stop.is_set():
            for record in consumer:
                from anp.contracts import Envelope

                try:
                    env = Envelope.model_validate(record.value)
                except Exception:  # noqa: BLE001
                    executor.dropped_invalid += 1
                    continue
                ack = executor.handle_command(env)
                if ack is not None:
                    executor.publish_ack(producer, ack, target_agent_id=env.source.agent_id)
                    print(f"[virtual-exec] cmd={ack.command_id[:8]} -> {ack.status.value}")
                if stop.is_set():
                    break
    finally:
        consumer.close()
        producer.flush()
        producer.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ANP v1 虚拟交通智能体（感知 + 执行）")
    ap.add_argument("--intersection", default=DEFAULT_INTERSECTION, help="路口 id")
    ap.add_argument("--interval", type=float, default=2.0, help="采样间隔（秒）")
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒），缺省永久")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（复现用）")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap，缺省取 ANP_BOOTSTRAP/localhost:9092")
    ap.add_argument("--no-exec", action="store_true", help="只跑感知，不跑命令执行/心跳")
    args = ap.parse_args()

    agent = VirtualTrafficAgent(intersection_id=args.intersection, interval_sec=args.interval, seed=args.seed)

    stop = threading.Event()
    threads: list[threading.Thread] = []
    world: WorldClient | None = None

    if not args.no_exec:
        # 自注册成世界公民（带 per-key 通道：本体只覆盖自己模拟的这个路口）。
        # 走世界级 WorldTopics（registry 双读 world+交通，本体即在统一名册可见）。
        world = WorldClient(
            VIRTUAL_AGENT_ID,
            agent_type="virtual",
            capabilities=AGENT_CAPABILITIES,
            command_types=AGENT_COMMAND_TYPES,
            produces=[
                Channel(topic=TrafficTopics.OBSERVATION, keys=[args.intersection]),
                Channel(topic=TrafficTopics.ACK, keys=[args.intersection]),
            ],
            consumes=[Channel(topic=TrafficTopics.COMMAND, keys=[args.intersection])],
            bootstrap=args.bootstrap,
        )
        world.register()
        print(f"[virtual] 已注册 {VIRTUAL_AGENT_ID}（perception+exec, 路口={args.intersection}）")
        world.start_heartbeat(DEFAULT_HEARTBEAT_SEC, stop)

        # 执行线程。
        executor = VirtualTrafficExecutor(agent_id=VIRTUAL_AGENT_ID)
        exec_thread = threading.Thread(
            target=_executor_loop, args=(executor, args.bootstrap, stop), name="virtual-exec", daemon=True
        )
        exec_thread.start()
        threads.append(exec_thread)

    try:
        sent = agent.run(duration_sec=args.duration, bootstrap=args.bootstrap)
        print(f"[virtual] 共发送 {sent} 条观测。")
    except KeyboardInterrupt:
        print("\n[virtual] 收到中断。")
    finally:
        stop.set()
        time.sleep(0.2)
        if world is not None:
            world.deregister()
            world.close()
            print(f"[virtual] 已下线 {VIRTUAL_AGENT_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
