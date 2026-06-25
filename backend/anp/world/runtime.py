"""ModelRuntime —— 通用 model 运行时（域无关）。

读一个 :class:`ModelSpec` → 用 :class:`WorldClient` 把 model **自注册成一个世界公民**
（``agent_type="model"``）+ 周期心跳 → 按成员通道建 consumer（**一 model 一 group**）→
把每条记录喂给注入的 ``workflow``（纯逻辑对象，约定 ``feed_record(value)`` /
可选 ``flush()``）→ 收尾下线。

Runtime 只接管「订阅 / 生命周期 / 自注册」；具体业务（聚合、决策…）在注入的 workflow
里，与 Runtime 解耦。例如交通路口状态聚合就把 ``SystemAgent`` 当 workflow 注入，
status 仍由 SystemAgent 自带 producer 产出，Runtime 不碰产出路径。
"""

from __future__ import annotations

import threading

from ..contracts import Channel, SourceSystem
from ..messaging import make_consumer
from .client import WorldClient
from .spec import ModelSpec


class ModelRuntime:
    def __init__(
        self,
        spec: ModelSpec,
        workflow,
        *,
        bootstrap: str | None = None,
        registry=None,
        producer=None,
        world_client: WorldClient | None = None,
    ) -> None:
        self.spec = spec
        self.workflow = workflow  # 注入的纯逻辑对象：feed_record(value) [+ flush()]
        self.bootstrap = bootstrap
        self.registry = registry  # 可选：subscribe_topics 留空时用它按成员 produces 推导
        subscribe = self._subscribe_topics()
        # 默认按 spec 通道自建 WorldClient（model 也是 agent）；可整体注入 world_client，
        # 或只注入 producer（测试用 FakeProducer）。
        self.client = world_client or WorldClient(
            spec.model_id,
            agent_type="model",
            capabilities=["model"],
            produces=[Channel(topic=t) for t in spec.produce_topics],
            consumes=[Channel(topic=t) for t in subscribe],
            weight=spec.weight,
            members=spec.member_agent_ids,  # 自报管辖成员 → 进世界名册
            source_system=SourceSystem.PLATFORM,
            bootstrap=bootstrap,
            producer=producer,
        )

    def _subscribe_topics(self) -> list[str]:
        """显式 subscribe_topics 优先；留空则由成员 agent 的 produces 求并集（需 registry）。"""

        if self.spec.subscribe_topics:
            return list(self.spec.subscribe_topics)
        if self.registry is None:
            return []
        topics: list[str] = []
        for aid in self.spec.member_agent_ids:
            rec = self.registry.get(aid)
            if rec is None:
                continue
            for ch in rec.produces:
                if ch.topic not in topics:
                    topics.append(ch.topic)
        return topics

    def run(self, *, stop: threading.Event | None = None, heartbeat_interval: float = 5.0) -> None:
        """自注册 + 心跳 + 消费成员通道喂 workflow，直到 consumer 迭代结束。"""

        topics = self._subscribe_topics()
        if not topics:
            raise SystemExit(f"model {self.spec.model_id} 无订阅 topic（spec.subscribe_topics 空且无 registry 可推导）")

        self.client.register()
        hb_stop = stop or threading.Event()
        self.client.start_heartbeat(heartbeat_interval, hb_stop)

        consumer = make_consumer(
            topics,
            group_id=self.spec.group_id,  # 一 model 一 group
            bootstrap_servers=self.bootstrap,
            auto_offset_reset="latest",
        )
        try:
            for record in consumer:
                self.workflow.feed_record(record.value)
        finally:
            if hasattr(self.workflow, "flush"):
                self.workflow.flush()
            hb_stop.set()
            try:
                self.client.deregister()
            except Exception:  # noqa: BLE001
                pass
            consumer.close()
            self.client.close()
