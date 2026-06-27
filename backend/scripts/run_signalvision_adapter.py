#!/usr/bin/env python3
"""运行 SignalVision 感知 adapter（接入真实 SV Dashboard → 感知层观测）。

每轮：取 SV `/api/simulation/status`（心跳携可达性）+ 各映射 junction 的
`/api/junctions/<id>`（→ 按方向观测，发布到 anp.traffic.perception.observation.v1）。
启动时注册（lifecycle），退出时下线。Ctrl-C 停止。

可替代 run_virtual_agent.py 的感知侧：配合 run_system_agent.py + run_gateway.py，
端到端把真实 SV 数据点亮网关同一路口（默认 SV junction `intersection_1_1` →
平台 `gg-xiongchu-minzu`，按需用 --junction/--intersection 覆盖）。

前置：SV Dashboard 在 --sv-base-url 可达；Kafka 已起、topic 已建。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_signalvision_adapter.py \
        --sv-base-url http://127.0.0.1:8080 --junction intersection_1_1 --intersection gg-xiongchu-minzu
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.adapters.signalvision import (  # noqa: E402
    DEFAULT_JUNCTION_MAP,
    DIRECTION_STRATEGY_AUTO,
    SV_ADAPTER_AGENT_ID,
    SignalVisionAdapter,
    SignalVisionAdapterConfig,
    build_signalvision_adapter_world_client,
    lifecycle_envelope,
)
from anp.contracts import TrafficTopics  # noqa: E402
from anp.messaging import make_producer, publish  # noqa: E402


def _build_config(args: argparse.Namespace) -> SignalVisionAdapterConfig:
    if args.junction and args.intersection:
        junction_map = {args.junction: args.intersection}
    else:
        junction_map = dict(DEFAULT_JUNCTION_MAP)
    return SignalVisionAdapterConfig(
        agent_id=args.agent_id,
        sv_base_url=args.sv_base_url,
        poll_interval_sec=args.interval,
        junction_map=junction_map,
        direction_strategy=args.strategy,
        confidence=args.confidence,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SignalVision 感知 adapter（感知接入）。")
    parser.add_argument("--bootstrap", default=None, help="Kafka bootstrap（默认 ANP_BOOTSTRAP/localhost:9092）")
    parser.add_argument("--sv-base-url", default="http://127.0.0.1:8080", help="SV Dashboard HTTP API 根地址")
    parser.add_argument("--agent-id", default=SV_ADAPTER_AGENT_ID)
    parser.add_argument("--junction", default=None, help="SV junction_id（与 --intersection 成对覆盖默认映射）")
    parser.add_argument("--intersection", default=None, help="平台 intersection_id")
    parser.add_argument("--strategy", default=DIRECTION_STRATEGY_AUTO, choices=["auto", "round_robin"])
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--interval", type=float, default=2.0, help="轮询间隔（秒）")
    parser.add_argument("--duration", type=float, default=None, help="运行时长（秒），默认永久")
    args = parser.parse_args()

    config = _build_config(args)
    print(
        f"[sv-adapter] agent_id={config.agent_id} sv={config.sv_base_url} "
        f"map={config.junction_map} strategy={config.direction_strategy}"
    )
    producer = make_producer(bootstrap_servers=args.bootstrap)
    world_client = build_signalvision_adapter_world_client(config, bootstrap=args.bootstrap, producer=producer)
    adapter = SignalVisionAdapter(config, world_client=world_client)

    # 注册（lifecycle）。
    publish(producer, TrafficTopics.AGENT_LIFECYCLE, lifecycle_envelope(agent_id=config.agent_id, registered=True))
    world_client.register()
    producer.flush()
    print("[sv-adapter] 已注册（lifecycle registered）")

    try:
        published = adapter.run(producer, duration_sec=args.duration)
        print(f"[sv-adapter] 退出，本次共发布观测 {published} 条")
    finally:
        # 下线（lifecycle deregister）。
        try:
            publish(
                producer,
                TrafficTopics.AGENT_LIFECYCLE,
                lifecycle_envelope(agent_id=config.agent_id, registered=False),
            )
            world_client.deregister()
            producer.flush()
            print("[sv-adapter] 已下线（lifecycle deregistered）")
        finally:
            world_client.close()
            producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
