#!/usr/bin/env python3
"""运行系统级智能体 traffic-system-001（live 模式）。

消费 anp.traffic.perception.observation.v1 → 10s 滚动窗口聚合 → 产出路口 World Status
到 anp.traffic.status.intersection.v1，并在内存维护每路口最新态。Ctrl-C 停止时
flush 残留窗口。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_system_agent.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.contracts import IntersectionStatusPayload  # noqa: E402
from anp.system_agent import build_default_agent  # noqa: E402


def _print_status(st: IntersectionStatusPayload) -> None:
    print(
        f"[system] {st.intersection_id} window[{st.window.start}..{st.window.end}] "
        f"samples={st.window.sample_count} queue={st.queue_length_m:.1f}m "
        f"flow={st.flow_veh_h:.0f}veh/h speed={st.mean_speed_kmh:.1f}km/h "
        f"delay={st.mean_delay_sec:.1f}s -> {st.congestion_level.value}",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="ANP 系统级智能体（观测 → World Status）")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap，缺省取 ANP_BOOTSTRAP/localhost:9092")
    args = ap.parse_args()

    agent, consumer = build_default_agent(bootstrap=args.bootstrap, on_status=_print_status)
    print(f"[system] {agent.agent_id} 启动，消费观测、产出 World Status… Ctrl-C 停止。")
    try:
        agent.run(consumer)
    except KeyboardInterrupt:
        print("\n[system] 收到中断，flush 残留窗口并退出。")
        agent.flush()
    finally:
        consumer.close()
    print(
        f"[system] 收尾：accepted={agent.accepted} windows_emitted={agent.windows_emitted} "
        f"dropped_late={agent.aggregator.dropped_late} dropped_conf={agent.dropped_confidence} "
        f"dropped_invalid={agent.dropped_invalid} 路口数={len(agent.store)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
