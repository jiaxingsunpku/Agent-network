"""交通 model 的 passthrough workflow（task5 第一步占位，followups F-1）。

第一步**不做窗口聚合**：把 per-junction 观测最小映射成 ``IntersectionStatusPayload`` 并
透传 ``sim_clock``，发到状态层 topic 供执行体订阅。映射（务实，第一步语义放宽见 followups）：

- ``halting_count → queue_length_m``（× 车均长 ``CAR_LEN_M``）：执行体按方向排队作压力。
- ``vehicle_count → flow_veh_h``（第一步瞬时车数，非吞吐）。
- ``mean_speed_mps → mean_speed_kmh``（× 3.6）。
- congestion 粗略（按总排队估）。

未来（F-1）：升级为实质语义聚合（窗口/清洗/对齐），或接 ``SystemAgent``。
workflow 协议：``feed_record(value)`` [+ ``flush()``]，由 ``anp.world.ModelRuntime`` 驱动。
"""

from __future__ import annotations

from ..contracts import (
    ApproachStatus,
    CongestionLevel,
    Envelope,
    EventType,
    GlobalTrafficStatusPayload,
    IntersectionStatusPayload,
    ObservationPayload,
    SequenceGenerator,
    StatusWindow,
    TrafficTopics,
    global_status_envelope,
    parse_payload,
    status_envelope,
)
from ..messaging import publish

CAR_LEN_M = 7.0
PASSTHROUGH_AGENT_ID = "traffic-model-passthrough"


def _congestion(index: float) -> CongestionLevel:
    if index >= 0.75:
        return CongestionLevel.SEVERE
    if index >= 0.5:
        return CongestionLevel.CONGESTED
    if index >= 0.25:
        return CongestionLevel.SLOW
    return CongestionLevel.SMOOTH


class PassthroughWorkflow:
    """观测 → 最小整形 → 状态 topic（第一步占位，不聚合窗口）。"""

    def __init__(self, producer, status_topic: str | None = None, agent_id: str = PASSTHROUGH_AGENT_ID,
                 global_topic: str | None = None, emit_global_every: int = 70) -> None:
        self.producer = producer
        self.status_topic = status_topic or TrafficTopics.STATUS_INTERSECTION
        self.global_topic = global_topic or TrafficTopics.STATUS_GLOBAL
        self.agent_id = agent_id
        self.emit_global_every = max(1, emit_global_every)
        self._seq = SequenceGenerator()
        self._gseq = SequenceGenerator()
        self.emitted = 0
        self.dropped = 0
        self.global_emitted = 0
        # task5 P-10：per-junction 最新聚合（车辆 / 等待 / 速度×车辆和），供全局总览（共识由系统级算）
        self._junction_latest: dict[str, tuple[int, int, float]] = {}
        self._obs_count = 0

    def feed_record(self, value) -> None:
        try:
            env = Envelope.model_validate(value)
        except Exception:  # noqa: BLE001 - 非法消息丢弃计数
            self.dropped += 1
            return
        if env.event_type != EventType.OBSERVATION_TRAFFIC_INTERSECTION:
            return
        try:
            obs = parse_payload(env)
        except Exception:  # noqa: BLE001
            self.dropped += 1
            return
        assert isinstance(obs, ObservationPayload)
        status = self._to_status(obs, env.time.event_ts)
        out = status_envelope(
            agent_id=self.agent_id, payload=status, event_ts=env.time.event_ts, sequence=self._seq.next()
        )
        publish(self.producer, self.status_topic, out)
        self.emitted += 1
        # task5 P-10：全局总览聚合（每 emit_global_every 个观测发一次共识全局）
        self._update_global(obs)
        self._obs_count += 1
        if self._obs_count % self.emit_global_every == 0:
            self._emit_global(obs.sim_clock, env.time.event_ts)

    def _to_status(self, obs: ObservationPayload, event_ts: str) -> IntersectionStatusPayload:
        approaches = []
        total_q = 0.0
        total_flow = 0.0
        sp_sum = 0.0
        for a in obs.approaches:
            q = float(a.halting_count) * CAR_LEN_M
            approaches.append(
                ApproachStatus(
                    direction=a.direction,
                    queue_length_m=q,
                    flow_veh_h=float(a.vehicle_count),
                    mean_speed_kmh=a.mean_speed_mps * 3.6,
                )
            )
            total_q += q
            total_flow += float(a.vehicle_count)
            sp_sum += a.mean_speed_mps * 3.6
        n = max(1, len(obs.approaches))
        idx = min(total_q / 200.0, 1.0)  # 粗略拥堵度（第一步，followups F-1）
        window = StatusWindow(start=event_ts, end=event_ts, size_sec=1, sample_count=1)
        return IntersectionStatusPayload(
            intersection_id=obs.intersection_id,
            window=window,
            queue_length_m=total_q,
            flow_veh_h=total_flow,
            mean_speed_kmh=sp_sum / n,
            mean_delay_sec=0.0,
            congestion_level=_congestion(idx),
            congestion_index=idx,
            approaches=approaches,
            sim_clock=obs.sim_clock,
        )

    def _update_global(self, obs: ObservationPayload) -> None:
        veh = sum(a.vehicle_count for a in obs.approaches)
        halt = sum(a.halting_count for a in obs.approaches)
        sp_sum = sum(a.mean_speed_mps * a.vehicle_count for a in obs.approaches)  # 速度×车辆，供加权均
        self._junction_latest[obs.intersection_id] = (veh, halt, sp_sum)

    def _emit_global(self, sim_clock, event_ts: str) -> None:
        jc = len(self._junction_latest)
        tv = sum(v[0] for v in self._junction_latest.values())
        th = sum(v[1] for v in self._junction_latest.values())
        sp_sum = sum(v[2] for v in self._junction_latest.values())
        mean_kmh = (sp_sum / tv * 3.6) if tv > 0 else 0.0
        payload = GlobalTrafficStatusPayload(
            junction_count=jc, total_vehicles=tv, total_halting=th, mean_speed_kmh=mean_kmh, sim_clock=sim_clock,
        )
        out = global_status_envelope(
            agent_id=self.agent_id, payload=payload, event_ts=event_ts, sequence=self._gseq.next()
        )
        publish(self.producer, self.global_topic, out)
        self.global_emitted += 1

    def flush(self) -> None:
        try:
            self.producer.flush()
        except Exception:  # noqa: BLE001
            pass
