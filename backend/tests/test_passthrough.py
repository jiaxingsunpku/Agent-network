"""task5：交通 model passthrough workflow 测试（observation → status 最小映射）。"""

from __future__ import annotations

from anp import contracts as c
from anp.system_agent.passthrough import PassthroughWorkflow


class FakeProducer:
    """记录 send 调用、不连 Kafka（仿 test_gateway 的注入式 producer）。"""

    def __init__(self) -> None:
        self.records = []

    def send(self, topic, key=None, value=None):
        self.records.append((topic, key, value))

        class _F:
            def get(self, timeout=None):
                return None

        return _F()

    def flush(self, *a, **k):
        pass


def _obs_env(iid: str = "J1", sim_step: int = 12):
    obs = c.ObservationPayload(
        intersection_id=iid,
        approaches=[
            c.Approach(direction=c.Direction.NORTH, vehicle_count=3, halting_count=5, mean_speed_mps=10.0),
            c.Approach(direction=c.Direction.EAST, vehicle_count=1, halting_count=0, mean_speed_mps=12.0),
        ],
        sim_clock=c.SimClock(sim_time=float(sim_step), sim_step=sim_step),
    )
    return c.observation_envelope(agent_id="traffic-perception-sv-j" + iid, payload=obs)


def test_passthrough_maps_observation_to_status():
    prod = FakeProducer()
    wf = PassthroughWorkflow(producer=prod)
    wf.feed_record(_obs_env().to_wire())

    assert wf.emitted == 1
    assert len(prod.records) == 1
    topic, key, value = prod.records[0]
    assert topic == c.TrafficTopics.STATUS_INTERSECTION
    assert key == "J1"  # 状态层按 intersection_id 分区
    env = c.Envelope.model_validate(value)
    assert env.event_type == c.EventType.STATUS_TRAFFIC_INTERSECTION
    st = c.parse_payload(env)
    assert isinstance(st, c.IntersectionStatusPayload)
    assert st.intersection_id == "J1"
    assert st.sim_clock is not None and st.sim_clock.sim_step == 12  # sim_clock 透传
    north = [a for a in st.approaches if a.direction == c.Direction.NORTH][0]
    assert north.queue_length_m == 35.0  # halting 5 × 7m
    assert north.flow_veh_h == 3.0  # vehicle_count（第一步瞬时车数）


def test_passthrough_ignores_non_observation():
    prod = FakeProducer()
    wf = PassthroughWorkflow(producer=prod)
    status = c.IntersectionStatusPayload(
        intersection_id="J1",
        window=c.StatusWindow(start="2026-06-30T00:00:00.000Z", end="2026-06-30T00:00:01.000Z", size_sec=1, sample_count=1),
        queue_length_m=0, flow_veh_h=0, mean_speed_kmh=0, mean_delay_sec=0,
        congestion_level=c.CongestionLevel.SMOOTH, congestion_index=0.0,
    )
    wf.feed_record(c.status_envelope(agent_id="x", payload=status).to_wire())
    assert wf.emitted == 0 and len(prod.records) == 0


def test_passthrough_drops_malformed():
    prod = FakeProducer()
    wf = PassthroughWorkflow(producer=prod)
    wf.feed_record({"not": "an envelope"})
    assert wf.dropped == 1 and wf.emitted == 0
