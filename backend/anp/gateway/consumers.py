"""网关后台 Kafka 消费 —— 把状态层/ack/registry 喂进 :class:`GatewayState`。

网关进程独立于系统级智能体，因此自己消费：
- 状态层 ``status.intersection.v1`` → 维护自身 World Status 当前态 + trend；
- ``ack.v1`` → 回填命令日志（命令闭环展示）；
- ``agent.lifecycle/heartbeat`` → 刷新 registry 在线状态。

均为守护线程，进程退出即随之结束。解析/业务在纯逻辑里，循环只搬运记录。
"""

from __future__ import annotations

import threading

from pydantic import ValidationError

from ..contracts import (
    AckPayload,
    Envelope,
    EventType,
    IntersectionStatusPayload,
    TrafficTopics,
    parse_payload,
)
from ..messaging import make_consumer
from ..registry.service import (
    REGISTRY_HEARTBEAT_TOPICS,
    REGISTRY_LIFECYCLE_TOPICS,
    RegistryConsumer,
)
from .state import GatewayState


class _StatusConsumer:
    """状态层 → 网关 World Status 当前态 + trend。"""

    def __init__(self, state: GatewayState) -> None:
        self.state = state
        self.applied = 0

    def run(self, consumer) -> None:
        for record in consumer:
            try:
                env = Envelope.model_validate(record.value)
                if env.event_type != EventType.STATUS_TRAFFIC_INTERSECTION:
                    continue
                payload = parse_payload(env)
            except ValidationError:
                continue
            assert isinstance(payload, IntersectionStatusPayload)
            self.state.status_store.update(payload)
            total_flow = sum(s.flow_veh_h for s in self.state.status_store.all().values())
            self.state.append_trend(total_flow)
            self.applied += 1


class _AckConsumer:
    """ack 层 → 命令日志回填。"""

    def __init__(self, state: GatewayState) -> None:
        self.state = state
        self.applied = 0

    def run(self, consumer) -> None:
        for record in consumer:
            try:
                env = Envelope.model_validate(record.value)
                if env.event_type != EventType.COMMAND_ACK:
                    continue
                payload = parse_payload(env)
            except ValidationError:
                continue
            assert isinstance(payload, AckPayload)
            self.state.command_log.record_ack(
                command_id=payload.command_id,
                command_type=payload.command_type.value,
                status=payload.status.value,
                decision=payload.safety.decision if payload.safety else None,
                reason=payload.safety.reason if payload.safety else None,
                target_agent_id=env.source.agent_id,
                ack_time=env.time.event_ts,
            )
            self.applied += 1


class GatewayConsumers:
    """启动并持有网关三类后台消费线程。"""

    def __init__(self, state: GatewayState) -> None:
        self.state = state
        self._threads: list[threading.Thread] = []
        self._consumers: list[object] = []
        self._closing = False
        self.status = _StatusConsumer(state)
        self.ack = _AckConsumer(state)
        # lifecycle 与 heartbeat 分开消费（不同 offset 策略），共享同一 registry。
        self.registry_lc = RegistryConsumer(state.registry)
        self.registry_hb = RegistryConsumer(state.registry)

    def _guarded(self, name: str, svc, consumer):
        """线程入口：正常退出/停机静默；非停机期的异常打印一行，便于排障。"""

        try:
            svc.run(consumer)
        except Exception as exc:  # noqa: BLE001
            if not self._closing:
                print(f"[gateway] 消费线程 {name} 异常退出: {exc}")

    def start(self) -> None:
        bootstrap = self.state.config.bootstrap

        status_consumer = make_consumer(
            TrafficTopics.STATUS_INTERSECTION,
            group_id="anp-gateway-status",
            bootstrap_servers=bootstrap,
            auto_offset_reset="latest",
        )
        ack_consumer = make_consumer(
            TrafficTopics.ACK,
            group_id="anp-gateway-ack",
            bootstrap_servers=bootstrap,
            auto_offset_reset="latest",
        )
        registry_lc_consumer = make_consumer(
            REGISTRY_LIFECYCLE_TOPICS,  # 名册：world + 交通 lifecycle
            group_id="anp-gateway-registry-lc",
            bootstrap_servers=bootstrap,
            auto_offset_reset="earliest",  # 从头重建世界名册（低频，代价小）
        )
        registry_hb_consumer = make_consumer(
            REGISTRY_HEARTBEAT_TOPICS,  # 活性：world + 交通 heartbeat
            group_id="anp-gateway-registry-hb",
            bootstrap_servers=bootstrap,
            auto_offset_reset="latest",  # 只读当前心跳，跳过历史积压（不然 live 误判离线）
        )
        self._consumers = [status_consumer, ack_consumer, registry_lc_consumer, registry_hb_consumer]

        for name, svc, consumer in (
            ("status", self.status, status_consumer),
            ("ack", self.ack, ack_consumer),
            ("registry-lc", self.registry_lc, registry_lc_consumer),
            ("registry-hb", self.registry_hb, registry_hb_consumer),
        ):
            t = threading.Thread(target=self._guarded, args=(name, svc, consumer), name=f"gw-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._closing = True
        for consumer in self._consumers:
            try:
                consumer.close()
            except Exception:  # noqa: BLE001
                pass
