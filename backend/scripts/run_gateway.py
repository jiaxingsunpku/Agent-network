#!/usr/bin/env python3
"""运行网关（FastAPI）—— 纯读模型 + 命令入口（docs/gateway-api.md）。

启动后台消费线程（状态层/ack/registry）维护读模型，再用 uvicorn 托管 HTTP。
前端把 VITE_AGENT_NETWORK_API_BASE 指向本服务即可（P4）。

前置：Kafka 已起、topic 已建（deploy/README.md）。Ctrl-C 停止。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_gateway.py
    ANP_BOOTSTRAP=localhost:9092 run_gateway.py --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from anp.gateway import GatewayConsumers, GatewayState, create_app  # noqa: E402
from anp.gateway.config import GatewayConfig  # noqa: E402
from anp.messaging import make_producer  # noqa: E402
from anp.registry import seed_default_registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="ANP 网关（读模型 + 命令入口）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap，缺省取 ANP_BOOTSTRAP/localhost:9092")
    ap.add_argument("--no-consumers", action="store_true", help="不启动后台消费（仅静态拓扑/种子 registry）")
    args = ap.parse_args()

    cfg = GatewayConfig(bootstrap=args.bootstrap, with_consumers=not args.no_consumers)
    producer = None if args.no_consumers else make_producer(bootstrap_servers=args.bootstrap)
    state = GatewayState(config=cfg, registry=seed_default_registry(), producer=producer)

    consumers = None
    if cfg.with_consumers:
        consumers = GatewayConsumers(state)
        consumers.start()
        print("[gateway] 后台消费已启动：status / ack / registry")

    app = create_app(state)

    # P7：把视频文本问答路由 co-host 到网关进程（前端复用现有 /api/* 反代）。
    # 逻辑独立在 anp/video，不混入交通域世界状态计算（AGENTS.md §3.4）。
    from anp.video.routes import include_video_routes  # noqa: E402

    vstore, _ = include_video_routes(app)
    print(f"[gateway] 已挂载视频文本问答 /api/agent-network/video-text/*（库内 {vstore.count()} 条）")

    print(f"[gateway] 监听 http://{args.host}:{args.port}/api/agent-network …")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if consumers is not None:
            consumers.stop()
        if producer is not None:
            producer.flush()
            producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
