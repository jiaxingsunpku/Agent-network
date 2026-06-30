#!/usr/bin/env python3
"""运行一个 model（工作流）：读 ModelSpec → ModelRuntime 跑注入的 workflow。

model 自注册成世界公民（agent_type="model"）+ 心跳，按 spec 订成员通道（一 model 一
consumer group），把记录喂给 workflow 产出结果。今天支持 ``workflow="system_agent"``
（交通路口状态聚合，复用 SystemAgent 纯逻辑、status 仍由其自带 producer 产出）。

用法::

    python backend/scripts/run_model.py --spec specs/traffic_system.json

注意：勿与 run_system_agent.py 同时起——两者 group 不同，会各自消费同一观测各产一份
status，输出翻倍（非 bug 但易误判）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 让 import anp.* 可用

from anp.messaging import make_producer  # noqa: E402
from anp.system_agent import SystemAgent  # noqa: E402
from anp.world import ModelRuntime, load_model_spec  # noqa: E402


def _build_workflow(name: str, *, producer, status_topic: str | None):
    """把 spec.workflow 名映射到具体纯逻辑 workflow 对象（协议：feed_record [+ flush]）。"""

    if name == "system_agent":
        kwargs = {"producer": producer}
        if status_topic:
            kwargs["status_topic"] = status_topic
        return SystemAgent(**kwargs)
    if name == "passthrough":  # task5：观测→最小整形→状态 topic（第一步占位）
        from anp.system_agent.passthrough import PassthroughWorkflow

        kwargs = {"producer": producer}
        if status_topic:
            kwargs["status_topic"] = status_topic
        return PassthroughWorkflow(**kwargs)
    raise SystemExit(f"未知 workflow: {name!r}（支持 'system_agent' / 'passthrough'）")


def main() -> int:
    ap = argparse.ArgumentParser(description="运行一个 ANP model（工作流）")
    ap.add_argument("--spec", required=True, help="ModelSpec JSON 路径")
    ap.add_argument("--bootstrap", default=None, help="Kafka bootstrap，缺省取 ANP_BOOTSTRAP/localhost:9092")
    args = ap.parse_args()

    spec = load_model_spec(args.spec)
    producer = make_producer(bootstrap_servers=args.bootstrap)
    status_topic = spec.produce_topics[0] if spec.produce_topics else None
    workflow = _build_workflow(spec.workflow, producer=producer, status_topic=status_topic)

    runtime = ModelRuntime(spec, workflow, bootstrap=args.bootstrap)
    print(
        f"[model] {spec.model_id} 启动：治「{spec.problem}」，"
        f"管辖 {spec.member_agent_ids}，订 {spec.subscribe_topics}，产 {spec.produce_topics}"
    )
    try:
        runtime.run()
    except KeyboardInterrupt:
        print("\n[model] 收到中断。")
    finally:
        producer.flush()
        producer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
