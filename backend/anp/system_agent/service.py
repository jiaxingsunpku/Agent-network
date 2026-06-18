"""系统级智能体装配 —— 消费观测 → 过滤 → 窗口聚合 → 算 World Status → 发布 + 当前态。

把「纯管道」（:meth:`feed_envelope`，无 Kafka 也能单测）与「Kafka 循环」（:meth:`run`）
拆开：管道负责契约校验、置信度过滤、按 event_ts 切窗、关窗结算、写当前态、可选发布；
循环只负责把 consumer 的记录喂给管道，并在收尾 flush 残留窗口。

边界：只产出路口级当前态，不做跨路口推理、不落库、不服务 HTTP（docs/architecture.md §4/§5）。
"""

from __future__ import annotations

from typing import Callable

from pydantic import ValidationError

from ..contracts import (
    Envelope,
    EventType,
    IntersectionStatusPayload,
    ObservationPayload,
    SequenceGenerator,
    TrafficTopics,
    iso_utc,
    parse_iso,
    parse_payload,
    status_envelope,
)
from ..messaging import make_consumer, make_producer, publish
from .compute import compute_status
from .constants import MIN_CONFIDENCE, SYSTEM_AGENT_ID
from .state import LatestStatusStore
from .windowing import ClosedWindow, WindowAggregator


class SystemAgent:
    """``traffic-system-001``：观测 → World Status 的窗口聚合服务。

    ``producer`` 为 ``None`` 时只更新内存当前态、不发布（便于不依赖 Kafka 的单测）。
    """

    def __init__(
        self,
        *,
        agent_id: str = SYSTEM_AGENT_ID,
        producer=None,
        store: LatestStatusStore | None = None,
        aggregator: WindowAggregator | None = None,
        min_confidence: float = MIN_CONFIDENCE,
        status_topic: str = TrafficTopics.STATUS_INTERSECTION,
        on_status: Callable[[IntersectionStatusPayload], None] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.producer = producer
        self.store = store or LatestStatusStore()
        self.aggregator = aggregator or WindowAggregator()
        self.min_confidence = min_confidence
        self.status_topic = status_topic
        #: 每结算一个窗口的回调（运维可观测 / 后续网关订阅当前态用）。
        self.on_status = on_status
        self._seq = SequenceGenerator()
        # 观测计数（运维可见性）。
        self.accepted = 0
        self.dropped_confidence = 0
        self.dropped_invalid = 0
        self.windows_emitted = 0

    # -- 纯管道：单条 envelope → 结算出的状态列表 -------------------------- #
    def feed_envelope(self, env: Envelope) -> list[IntersectionStatusPayload]:
        """处理一条已解析 envelope，返回因其推进而结算的 World Status（可能为空）。"""

        if env.event_type != EventType.OBSERVATION_TRAFFIC_INTERSECTION:
            return []  # 状态层只消费观测，其余忽略
        if env.quality.confidence < self.min_confidence:
            self.dropped_confidence += 1
            return []
        try:
            payload = parse_payload(env)
        except ValidationError:
            self.dropped_invalid += 1
            return []
        assert isinstance(payload, ObservationPayload)

        self.accepted += 1
        event_ts = parse_iso(env.time.event_ts)
        closed = self.aggregator.add(payload.intersection_id, event_ts, payload)
        return self._settle(closed)

    def feed_record(self, value: dict) -> list[IntersectionStatusPayload]:
        """处理一条 Kafka 记录的值（dict）；非法外壳计数后丢弃。"""

        try:
            env = Envelope.model_validate(value)
        except ValidationError:
            self.dropped_invalid += 1
            return []
        return self.feed_envelope(env)

    def _settle(self, closed: list[ClosedWindow]) -> list[IntersectionStatusPayload]:
        out: list[IntersectionStatusPayload] = []
        for window in closed:
            status = compute_status(window)
            self.store.update(status)
            self._publish(status, event_ts=iso_utc(window.end))
            self.windows_emitted += 1
            if self.on_status is not None:
                self.on_status(status)
            out.append(status)
        return out

    def _publish(self, status: IntersectionStatusPayload, *, event_ts: str) -> None:
        if self.producer is None:
            return
        env = status_envelope(
            agent_id=self.agent_id,
            payload=status,
            event_ts=event_ts,
            sequence=self._seq.next(),
        )
        publish(self.producer, self.status_topic, env)

    def flush(self) -> list[IntersectionStatusPayload]:
        """结算所有残留窗口（关停 / 有限流末尾）。"""

        return self._settle(self.aggregator.flush_all())

    # -- Kafka 循环 -------------------------------------------------------- #
    def run(self, consumer, *, flush_on_stop: bool = True) -> None:
        """消费 ``consumer`` 直到其迭代结束（live: 永不结束；drain: 空闲超时结束）。"""

        try:
            for record in consumer:
                self.feed_record(record.value)
        finally:
            if flush_on_stop:
                self.flush()
            if self.producer is not None:
                self.producer.flush()


def build_default_agent(
    *,
    bootstrap: str | None = None,
    on_status: Callable[[IntersectionStatusPayload], None] | None = None,
) -> tuple[SystemAgent, object]:
    """构造一个连真实 Kafka 的系统级智能体 + 其 consumer（live 模式，永不超时）。

    返回 ``(agent, consumer)``；调用方负责 ``agent.run(consumer)`` 与关停清理。
    """

    producer = make_producer(bootstrap_servers=bootstrap)
    agent = SystemAgent(producer=producer, on_status=on_status)
    consumer = make_consumer(
        TrafficTopics.OBSERVATION,
        group_id="anp-system-agent",
        bootstrap_servers=bootstrap,
        auto_offset_reset="latest",
    )
    return agent, consumer
