"""WorldClient —— 接入方 SDK：自注册（带通道 + weight）、心跳、发布、订阅。

让接入方（交通组 / 视频组 / model）对着它写、不碰裸 Kafka：一个 agent 启动时
``register()``、周期 ``heartbeat()``、按通道 ``publish()`` / ``subscribe()``，退出
``deregister()``。所有 agent（含 model 本身）都往**世界级** :class:`WorldTopics`
报到，registry 读这两条 topic 重建统一世界名册（earliest + compacted lifecycle）。

只在 :mod:`anp.messaging`（统一 Kafka 收发口）与 :mod:`anp.contracts`（唯一契约源）
之上做薄封装，不含任何业务逻辑。
"""

from __future__ import annotations

import threading
from typing import Iterable

from ..contracts import (
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Channel,
    Envelope,
    EventType,
    SequenceGenerator,
    Source,
    SourceSystem,
    WorldTopics,
    make_envelope,
)
from ..messaging import make_consumer, make_producer, publish


class WorldClient:
    """一个世界公民的自注册 / 心跳 / 收发门面。

    ``producer`` 可注入（测试用 FakeProducer）；否则首次用到时按 ``bootstrap`` 懒构造，
    并由本对象在 :meth:`close` 时负责关闭。
    """

    def __init__(
        self,
        agent_id: str,
        *,
        agent_type: str,
        capabilities: Iterable[str] = (),
        command_types: Iterable[str] = (),
        produces: Iterable[Channel] = (),
        consumes: Iterable[Channel] = (),
        weight: float = 1.0,
        source_system: SourceSystem = SourceSystem.COLLABORATIVE_AGENT,
        bootstrap: str | None = None,
        producer=None,
    ) -> None:
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.capabilities = list(capabilities)
        self.command_types = list(command_types)
        self.produces = list(produces)
        self.consumes = list(consumes)
        self.weight = weight
        self.source_system = source_system
        self.bootstrap = bootstrap
        self._producer = producer
        self._owns_producer = producer is None
        self._seq = SequenceGenerator()

    # -- producer（懒构造）------------------------------------------------- #
    @property
    def producer(self):
        if self._producer is None:
            self._producer = make_producer(bootstrap_servers=self.bootstrap)
        return self._producer

    def _source(self) -> Source:
        return Source(system=self.source_system, agent_id=self.agent_id)

    # -- 生命周期 ---------------------------------------------------------- #
    def _lifecycle_envelope(self, *, registered: bool) -> Envelope:
        payload = AgentLifecyclePayload(
            agent_id=self.agent_id,
            agent_type=self.agent_type,
            capabilities=self.capabilities,
            command_types=self.command_types,
            produces=self.produces,
            consumes=self.consumes,
            weight=self.weight,
        )
        return make_envelope(
            event_type=EventType.AGENT_REGISTERED if registered else EventType.AGENT_DEREGISTERED,
            source=self._source(),
            payload=payload,
            sequence=self._seq.next(),
        )

    def register(self) -> None:
        """发 AGENT_REGISTERED 到世界名册（key=agent_id，compacted 留最新一条）。"""

        publish(self.producer, WorldTopics.AGENT_LIFECYCLE, self._lifecycle_envelope(registered=True), flush=True)

    def deregister(self) -> None:
        publish(self.producer, WorldTopics.AGENT_LIFECYCLE, self._lifecycle_envelope(registered=False), flush=True)

    # -- 心跳 -------------------------------------------------------------- #
    def heartbeat(self, status: str = "online", last_error: str | None = None) -> None:
        env = make_envelope(
            event_type=EventType.AGENT_HEARTBEAT,
            source=self._source(),
            payload=AgentHeartbeatPayload(status=status, last_error=last_error),
            sequence=self._seq.next(),
        )
        publish(self.producer, WorldTopics.AGENT_HEARTBEAT, env, flush=True)

    def start_heartbeat(
        self, interval: float, stop: threading.Event, *, status: str = "online"
    ) -> threading.Thread:
        """起一个守护线程周期发心跳，直到 ``stop`` 置位。返回该线程。"""

        def _loop() -> None:
            while not stop.is_set():
                try:
                    self.heartbeat(status=status)
                except Exception as exc:  # noqa: BLE001 - 心跳失败不应拖垮主流程
                    print(f"[world] {self.agent_id} 心跳异常: {exc}")
                stop.wait(interval)

        t = threading.Thread(target=_loop, name=f"world-hb-{self.agent_id}", daemon=True)
        t.start()
        return t

    # -- 收发透传 ---------------------------------------------------------- #
    def publish(self, topic: str, env: Envelope, *, flush: bool = False):
        return publish(self.producer, topic, env, flush=flush)

    def subscribe(
        self, topics, *, group_id: str | None = None, auto_offset_reset: str = "latest", **overrides
    ):
        return make_consumer(
            topics,
            group_id=group_id,
            bootstrap_servers=self.bootstrap,
            auto_offset_reset=auto_offset_reset,
            **overrides,
        )

    def close(self) -> None:
        if self._producer is not None and self._owns_producer:
            try:
                self._producer.flush()
                self._producer.close()
            except Exception:  # noqa: BLE001
                pass
            self._producer = None
