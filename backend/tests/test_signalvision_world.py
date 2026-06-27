"""SignalVision WorldClient transition tests (no Kafka)."""

from __future__ import annotations

from anp import contracts as c
from anp.adapters.signalvision import (
    EXEC_COMMAND_TYPES,
    SignalVisionAdapter,
    SignalVisionAdapterConfig,
    SignalVisionExecConfig,
    build_signalvision_adapter_world_client,
    build_signalvision_exec_world_client,
)


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

        class _Future:
            def get(self, *args, **kwargs):
                return None

        return _Future()

    def flush(self):
        pass

    def close(self):
        pass


def _last_payload(fake: FakeProducer):
    env = c.Envelope.model_validate(fake.sent[-1][2])
    return env, c.parse_payload(env)


def test_sv_adapter_worldclient_registers_per_key_observation_channel():
    fake = FakeProducer()
    cfg = SignalVisionAdapterConfig(
        agent_id="traffic-perception-sv-test",
        junction_map={"1": "gg-xiongchu-minzu", "2": "gg-xiongchu-guanggu", "3": "gg-xiongchu-minzu"},
    )
    wc = build_signalvision_adapter_world_client(cfg, producer=fake)

    wc.register()

    topic, key, _ = fake.sent[-1]
    env, payload = _last_payload(fake)
    assert topic == c.WorldTopics.AGENT_LIFECYCLE
    assert key == cfg.agent_id
    assert env.event_type == c.EventType.AGENT_REGISTERED
    assert isinstance(payload, c.AgentLifecyclePayload)
    assert payload.agent_id == cfg.agent_id
    assert payload.agent_type == "signalvision"
    assert payload.capabilities == ["perception"]
    assert payload.command_types == []
    assert [(ch.topic, ch.keys) for ch in payload.produces] == [
        (c.TrafficTopics.OBSERVATION, ["gg-xiongchu-minzu", "gg-xiongchu-guanggu"])
    ]
    assert payload.consumes == []


def test_sv_executor_worldclient_registers_command_and_ack_channels():
    fake = FakeProducer()
    cfg = SignalVisionExecConfig(
        agent_id="traffic-exec-sv-test",
        junction_map={"1": "gg-xiongchu-minzu", "2": "gg-xiongchu-guanggu"},
    )
    wc = build_signalvision_exec_world_client(cfg, producer=fake)

    wc.register()

    topic, key, _ = fake.sent[-1]
    env, payload = _last_payload(fake)
    assert topic == c.WorldTopics.AGENT_LIFECYCLE
    assert key == cfg.agent_id
    assert env.event_type == c.EventType.AGENT_REGISTERED
    assert isinstance(payload, c.AgentLifecyclePayload)
    assert payload.capabilities == ["exec"]
    assert payload.command_types == list(EXEC_COMMAND_TYPES)
    assert [(ch.topic, ch.keys) for ch in payload.consumes] == [
        (c.TrafficTopics.COMMAND, ["gg-xiongchu-minzu", "gg-xiongchu-guanggu"])
    ]
    assert [(ch.topic, ch.keys) for ch in payload.produces] == [(c.TrafficTopics.ACK, [])]


def test_sv_adapter_heartbeat_is_mirrored_to_world_topic():
    fake = FakeProducer()
    cfg = SignalVisionAdapterConfig(agent_id="traffic-perception-sv-test")
    wc = build_signalvision_adapter_world_client(cfg, producer=fake)
    adapter = SignalVisionAdapter(cfg, world_client=wc)

    adapter.publish_heartbeat(fake, reachable=False, last_error="SV down")

    topics = [row[0] for row in fake.sent]
    assert topics == [c.TrafficTopics.AGENT_HEARTBEAT, c.WorldTopics.AGENT_HEARTBEAT]
    traffic_hb = c.parse_payload(c.Envelope.model_validate(fake.sent[0][2]))
    world_hb = c.parse_payload(c.Envelope.model_validate(fake.sent[1][2]))
    assert traffic_hb.status == world_hb.status == "degraded"
    assert traffic_hb.last_error == world_hb.last_error == "SV down"
