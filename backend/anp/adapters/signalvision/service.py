"""SignalVision 感知 adapter 服务编排。

把 :class:`SignalVisionClient` 取到的 junction 状态经 :mod:`mapping` 映射成按方向
观测，再经**统一 envelope builder**（``observation_envelope``）发布到感知层 topic；
另发心跳（携 SV 可达性）与上下线。**不在此散搓 envelope、不算 World Status。**

与 ``agents/virtual_traffic.py`` 一致的分层：纯映射在 :mod:`mapping`（可单测），
本类管状态（每 junction 累计通过量基线、序号）+ 一轮 IO（poll→map→publish）+ 循环。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from anp.contracts import (
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Envelope,
    EventType,
    ObservationPayload,
    Quality,
    SequenceGenerator,
    Source,
    SourceSystem,
    TrafficTopics,
    make_envelope,
    now_iso,
    observation_envelope,
)
from anp.messaging import publish

from .client import SignalVisionClient
from .config import SignalVisionAdapterConfig
from .mapping import map_junction_to_observation

#: 本 adapter 声明的能力（纯感知；不接命令，command_types 为空）。
SV_CAPABILITIES = ("perception",)
SV_COMMAND_TYPES: tuple[str, ...] = ()


@dataclass
class PollResult:
    """一轮轮询结果（供脚本日志与冒烟断言）。"""

    reachable: bool
    published: int = 0
    skipped: int = 0
    intersections: list[str] = field(default_factory=list)
    last_error: str | None = None


class SignalVisionAdapter:
    """SignalVision → 感知层观测的接入适配器。"""

    def __init__(
        self,
        config: SignalVisionAdapterConfig | None = None,
        *,
        client: SignalVisionClient | None = None,
    ) -> None:
        self.config = config or SignalVisionAdapterConfig()
        self.client = client or SignalVisionClient(
            self.config.sv_base_url, timeout_sec=self.config.http_timeout_sec
        )
        self._seq = SequenceGenerator()
        #: 每 junction 上一轮累计通过量，用于差分出本间隔通过量。
        self._prev_passed: dict[str, int] = {}

    # -- 纯映射（含通过量差分状态，无 IO，可单测）------------------------- #
    def map_detail(
        self, junction_id: str, junction: dict
    ) -> tuple[str, ObservationPayload] | None:
        """把一个 junction 状态字典映射成 ``(intersection_id, 观测)``。

        未在 ``junction_map`` 内、或无进口车道时返回 ``None``。会更新本 junction 的
        累计通过量基线。
        """

        intersection_id = self.config.junction_map.get(junction_id)
        if intersection_id is None:
            return None
        payload, passed_now = map_junction_to_observation(
            junction,
            intersection_id,
            prev_passed=self._prev_passed.get(junction_id),
            strategy=self.config.direction_strategy,
        )
        self._prev_passed[junction_id] = passed_now
        if payload is None:
            return None
        return intersection_id, payload

    # -- 发布 -------------------------------------------------------------- #
    def publish_observation(
        self, producer, intersection_id: str, payload: ObservationPayload, *, event_ts: str | None = None
    ) -> str:
        ts = event_ts or now_iso()
        env = observation_envelope(
            agent_id=self.config.agent_id,
            payload=payload,
            site_id=self.config.site_id,
            region_id=self.config.region_id,
            event_ts=ts,
            sequence=self._seq.next(),
            quality=Quality(confidence=self.config.confidence),
        )
        publish(producer, TrafficTopics.OBSERVATION, env)
        return ts

    def publish_heartbeat(self, producer, *, reachable: bool, last_error: str | None = None) -> None:
        status = "online" if reachable else "degraded"
        env = heartbeat_envelope(
            agent_id=self.config.agent_id,
            status=status,
            last_error=last_error,
            sequence=self._seq.next(),
        )
        publish(producer, TrafficTopics.AGENT_HEARTBEAT, env)

    # -- 一轮：取 status（心跳）+ 各 junction detail（观测）-------------- #
    def poll_once(self, producer, *, event_ts: str | None = None) -> PollResult:
        status = self.client.get_status()
        reachable = bool(status.ok)
        last_error = None if reachable else str(status.body.get("message") or "SV API 不可达")
        self.publish_heartbeat(producer, reachable=reachable, last_error=last_error)

        result = PollResult(reachable=reachable, last_error=last_error)
        for junction_id in self.config.junction_map:
            junction = self.client.junction_state(junction_id)
            if junction is None:
                result.skipped += 1
                continue
            mapped = self.map_detail(junction_id, junction)
            if mapped is None:
                result.skipped += 1
                continue
            intersection_id, payload = mapped
            self.publish_observation(producer, intersection_id, payload, event_ts=event_ts)
            result.published += 1
            result.intersections.append(intersection_id)
        return result

    # -- 循环 -------------------------------------------------------------- #
    def run(self, producer, *, duration_sec: float | None = None) -> int:
        """定时轮询并发布，直到 ``duration_sec`` 用尽（None=永久）。返回已发观测数。"""

        published = 0
        start = time.monotonic()
        try:
            while duration_sec is None or (time.monotonic() - start) < duration_sec:
                res = self.poll_once(producer)
                published += res.published
                state = "online" if res.reachable else "degraded(SV 不可达)"
                print(
                    f"[sv-adapter] {state} published={res.published} skipped={res.skipped} "
                    f"intersections={res.intersections}"
                )
                producer.flush()
                time.sleep(self.config.poll_interval_sec)
        except KeyboardInterrupt:
            print("\n[sv-adapter] 收到中断，停止接入。")
        finally:
            producer.flush()
        return published


# --------------------------------------------------------------------------- #
# lifecycle / heartbeat envelope（与 agents/virtual_traffic.py 风格一致）
# --------------------------------------------------------------------------- #
def _adapter_source(agent_id: str) -> Source:
    return Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id)


def lifecycle_envelope(
    *,
    agent_id: str,
    registered: bool,
    agent_type: str = "signalvision",
    sequence: int = 0,
) -> Envelope:
    """注册/下线 envelope（topic anp.traffic.agent.lifecycle.v1）。纯感知，无 command_types。"""

    payload = AgentLifecyclePayload(
        agent_id=agent_id,
        agent_type=agent_type,
        capabilities=list(SV_CAPABILITIES),
        command_types=list(SV_COMMAND_TYPES),
    )
    return make_envelope(
        event_type=EventType.AGENT_REGISTERED if registered else EventType.AGENT_DEREGISTERED,
        source=_adapter_source(agent_id),
        payload=payload,
        sequence=sequence,
    )


def heartbeat_envelope(
    *, agent_id: str, status: str = "online", last_error: str | None = None, sequence: int = 0
) -> Envelope:
    """心跳 envelope（topic anp.traffic.agent.heartbeat.v1）。"""

    return make_envelope(
        event_type=EventType.AGENT_HEARTBEAT,
        source=_adapter_source(agent_id),
        payload=AgentHeartbeatPayload(status=status, last_error=last_error),
        sequence=sequence,
    )
