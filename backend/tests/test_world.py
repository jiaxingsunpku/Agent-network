"""world 包回归（纯内存，无 Kafka）：ModelSpec、WorldClient 自注册、订阅推导。

运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from anp import contracts as c
from anp.registry import Registry
from anp.world import ModelRuntime, ModelSpec, WorldClient, load_model_spec


class FakeProducer:
    """记录 send 调用的桩 producer（仿 test_gateway 的注入做法）。"""

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

        class _F:
            def get(self, *a, **k):
                return None

        return _F()

    def flush(self):
        pass

    def close(self):
        pass


def test_model_spec_group_id_and_load(tmp_path):
    spec = ModelSpec(
        model_id="traffic-control",
        workflow="system_agent",
        member_agent_ids=["traffic-virtual-001"],
        subscribe_topics=["t.obs"],
        produce_topics=["t.status"],
    )
    assert spec.group_id == "anp-model-traffic-control"

    p = tmp_path / "s.json"
    p.write_text(spec.model_dump_json(), encoding="utf-8")
    loaded = load_model_spec(p)
    assert loaded.model_id == "traffic-control"
    assert loaded.subscribe_topics == ["t.obs"]
    assert loaded.group_id == "anp-model-traffic-control"


def test_worldclient_register_emits_channels_keyed_by_agent_id():
    fake = FakeProducer()
    wc = WorldClient(
        "a",
        agent_type="virtual",
        capabilities=["perception"],
        command_types=["set_signal_plan"],
        produces=[c.Channel(topic="t.obs", keys=["int-1"])],
        consumes=[c.Channel(topic="t.cmd")],
        weight=2.0,
        producer=fake,
    )
    wc.register()
    topic, key, value = fake.sent[-1]
    assert topic == c.WorldTopics.AGENT_LIFECYCLE
    assert key == "a"  # 按 agent_id 做 key —— compaction「每 agent 留最新」前提
    env = c.Envelope.model_validate(value)
    assert env.event_type == c.EventType.AGENT_REGISTERED
    payload = c.parse_payload(env)
    assert isinstance(payload, c.AgentLifecyclePayload)
    assert payload.produces[0].keys == ["int-1"]
    assert payload.weight == 2.0

    wc.heartbeat()
    assert fake.sent[-1][0] == c.WorldTopics.AGENT_HEARTBEAT
    hb = c.parse_payload(c.Envelope.model_validate(fake.sent[-1][2]))
    assert isinstance(hb, c.AgentHeartbeatPayload) and hb.status == "online"

    wc.deregister()
    assert c.Envelope.model_validate(fake.sent[-1][2]).event_type == c.EventType.AGENT_DEREGISTERED


def test_model_runtime_self_registers_as_model_agent():
    fake = FakeProducer()
    spec = ModelSpec(
        model_id="x",
        workflow="system_agent",
        subscribe_topics=["t.obs"],
        produce_topics=["t.status"],
        weight=1.5,
    )
    # 只注入 producer，让 ModelRuntime 按 spec 通道自建 model 的 WorldClient。
    rt = ModelRuntime(spec, workflow=None, producer=fake)
    rt.client.register()
    payload = c.parse_payload(c.Envelope.model_validate(fake.sent[-1][2]))
    assert payload.agent_type == "model"
    assert payload.consumes[0].topic == "t.obs"
    assert payload.produces[0].topic == "t.status"


def test_model_runtime_derives_subscribe_from_member_produces():
    reg = Registry()
    reg.register(agent_id="m1", agent_type="virtual", produces=[c.Channel(topic="t.obs", keys=["int-1"])])
    spec = ModelSpec(
        model_id="x",
        workflow="system_agent",
        member_agent_ids=["m1"],
        produce_topics=["t.status"],  # subscribe_topics 留空 → 由成员 produces 推导
    )
    rt = ModelRuntime(spec, workflow=None, registry=reg, producer=FakeProducer())
    assert rt._subscribe_topics() == ["t.obs"]
