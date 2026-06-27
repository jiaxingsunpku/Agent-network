"""Registry 回归：注册 / 心跳 / 在线降级离线派生 / 命令白名单 / envelope 应用。

运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anp import contracts as c
from anp.registry import (
    DerivedStatus,
    Registry,
    seed_default_registry,
)
from anp.registry.constants import HEARTBEAT_OFFLINE_TTL_SEC, HEARTBEAT_ONLINE_TTL_SEC

BASE = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)


def test_seed_has_virtual_and_system():
    reg = seed_default_registry(now=BASE)
    ids = {r.agent_id for r in reg.all()}
    assert {"traffic-virtual-001", "traffic-system-001"} <= ids


def test_derived_status_syncing_then_online_degraded_offline():
    reg = Registry()
    reg.register(agent_id="a", agent_type="virtual", command_types=["set_signal_plan"], now=BASE)
    # 无心跳 → syncing。
    assert reg.get("a").derived_status(BASE) == DerivedStatus.SYNCING

    reg.heartbeat(agent_id="a", status="online", now=BASE)
    rec = reg.get("a")
    # 新鲜 → online。
    assert rec.derived_status(BASE) == DerivedStatus.ONLINE
    # 超在线 TTL 但未超离线 TTL → degraded。
    assert rec.derived_status(BASE + timedelta(seconds=HEARTBEAT_ONLINE_TTL_SEC + 1)) == DerivedStatus.DEGRADED
    # 超离线 TTL → offline。
    assert rec.derived_status(BASE + timedelta(seconds=HEARTBEAT_OFFLINE_TTL_SEC + 1)) == DerivedStatus.OFFLINE


def test_reported_degraded_respected_when_fresh():
    reg = Registry()
    reg.register(agent_id="a", agent_type="virtual", now=BASE)
    reg.heartbeat(agent_id="a", status="degraded", now=BASE)
    assert reg.get("a").derived_status(BASE) == DerivedStatus.DEGRADED


def test_authorize_command_whitelist():
    reg = seed_default_registry(now=BASE)
    # 虚拟体接收 set_signal_plan。
    assert reg.authorize_command("traffic-virtual-001", "set_signal_plan").allowed
    # 系统级智能体不接收命令。
    sys_authz = reg.authorize_command("traffic-system-001", "set_signal_plan")
    assert not sys_authz.allowed and sys_authz.code == "command_not_allowed_for_target"
    # 未知目标。
    ghost = reg.authorize_command("ghost-001", "set_signal_plan")
    assert not ghost.allowed and ghost.code == "target_not_whitelisted"


def test_apply_envelope_lifecycle_and_heartbeat():
    reg = Registry()
    lifecycle = c.make_envelope(
        event_type=c.EventType.AGENT_REGISTERED,
        source=c.Source(system=c.SourceSystem.COLLABORATIVE_AGENT, agent_id="traffic-virtual-001"),
        payload=c.AgentLifecyclePayload(
            agent_id="traffic-virtual-001",
            agent_type="virtual",
            capabilities=["perception", "exec"],
            command_types=["set_signal_plan"],
        ),
    )
    assert reg.apply_envelope(lifecycle, now=BASE)
    assert reg.get("traffic-virtual-001").command_types == ["set_signal_plan"]

    hb = c.make_envelope(
        event_type=c.EventType.AGENT_HEARTBEAT,
        source=c.Source(system=c.SourceSystem.COLLABORATIVE_AGENT, agent_id="traffic-virtual-001"),
        payload=c.AgentHeartbeatPayload(status="online"),
    )
    assert reg.apply_envelope(hb, now=BASE)
    assert reg.get("traffic-virtual-001").derived_status(BASE) == DerivedStatus.ONLINE

    dereg = c.make_envelope(
        event_type=c.EventType.AGENT_DEREGISTERED,
        source=c.Source(system=c.SourceSystem.COLLABORATIVE_AGENT, agent_id="traffic-virtual-001"),
        payload=c.AgentLifecyclePayload(agent_id="traffic-virtual-001", agent_type="virtual"),
    )
    assert reg.apply_envelope(dereg)
    assert reg.get("traffic-virtual-001").reported_status == "offline"


def test_apply_envelope_ignores_unrelated():
    reg = Registry()
    obs = c.observation_envelope(
        agent_id="traffic-virtual-001",
        payload=c.ObservationPayload(
            intersection_id="X",
            approaches=[c.Approach(direction=c.Direction.NORTH, vehicle_count=1, halting_count=1, mean_speed_mps=5.0)],
        ),
    )
    assert reg.apply_envelope(obs) is False


def test_register_with_channels_and_catalog():
    reg = Registry()
    reg.register(
        agent_id="a",
        agent_type="virtual",
        capabilities=["perception"],
        produces=[c.Channel(topic="t.obs", keys=["int-1"])],
        consumes=[c.Channel(topic="t.cmd", keys=["int-1"])],
        weight=2.0,
        now=BASE,
    )
    rec = reg.get("a")
    assert rec.weight == 2.0 and rec.produces[0].topic == "t.obs"

    cat = reg.catalog_by_topic()
    assert "a" in cat["t.obs"]["producers"]
    assert "a" in cat["t.obs"]["keys"]["int-1"]["producers"]
    assert "a" in cat["t.cmd"]["consumers"]

    # 覆盖查询：给 key 只命中覆盖该实体的；通道声明了 keys 则不覆盖其它 key。
    assert [r.agent_id for r in reg.agents_covering("t.obs", "int-1")] == ["a"]
    assert [r.agent_id for r in reg.agents_covering("t.obs")] == ["a"]
    assert reg.agents_covering("t.obs", "other") == []
    assert [r.agent_id for r in reg.agents_with_capability("perception")] == ["a"]


def test_register_preserves_channels_when_not_provided():
    reg = Registry()
    reg.register(agent_id="a", agent_type="virtual", produces=[c.Channel(topic="t.obs")], weight=3.0, now=BASE)
    # 重复注册只刷新能力、不带通道 → 通道/weight 保留。
    reg.register(agent_id="a", agent_type="virtual", capabilities=["x"], now=BASE)
    rec = reg.get("a")
    assert rec.produces and rec.produces[0].topic == "t.obs" and rec.weight == 3.0


def test_register_empty_channels_do_not_clear_world_transition_metadata():
    reg = Registry()
    reg.register(
        agent_id="traffic-exec-sv-001",
        agent_type="signalvision",
        capabilities=["exec"],
        command_types=["control_signal_inference"],
        produces=[c.Channel(topic="t.ack")],
        consumes=[c.Channel(topic="t.command", keys=["gg-xiongchu-minzu"])],
        now=BASE,
    )
    # 旧 traffic lifecycle 在过渡期可能晚于 world lifecycle 到达，并带空通道。
    reg.register(
        agent_id="traffic-exec-sv-001",
        agent_type="signalvision",
        capabilities=["exec"],
        command_types=["control_signal_inference"],
        produces=[],
        consumes=[],
        now=BASE,
    )

    rec = reg.get("traffic-exec-sv-001")
    assert [(ch.topic, ch.keys) for ch in rec.produces] == [("t.ack", [])]
    assert [(ch.topic, ch.keys) for ch in rec.consumes] == [("t.command", ["gg-xiongchu-minzu"])]


def test_apply_envelope_carries_channels_and_weight():
    reg = Registry()
    env = c.make_envelope(
        event_type=c.EventType.AGENT_REGISTERED,
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="m"),
        payload=c.AgentLifecyclePayload(
            agent_id="m",
            agent_type="model",
            capabilities=["model"],
            produces=[c.Channel(topic="t.status")],
            consumes=[c.Channel(topic="t.obs")],
            weight=1.5,
        ),
    )
    assert reg.apply_envelope(env, now=BASE)
    rec = reg.get("m")
    assert rec.weight == 1.5 and rec.produces[0].topic == "t.status"
    assert "m" in reg.catalog_by_topic()["t.status"]["producers"]


def test_models_and_governed_by():
    reg = Registry()
    reg.register(agent_id="leaf", agent_type="virtual", now=BASE)
    reg.register(agent_id="m1", agent_type="model", members=["leaf"], now=BASE)
    reg.register(agent_id="m2", agent_type="model", members=["leaf"], now=BASE)
    assert {r.agent_id for r in reg.models()} == {"m1", "m2"}
    assert sorted(reg.governed_by("leaf")) == ["m1", "m2"]  # 共享成员
    assert reg.governed_by("m1") == []

    # members 经 lifecycle 自报 → 进 record。
    env = c.make_envelope(
        event_type=c.EventType.AGENT_REGISTERED,
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="m3"),
        payload=c.AgentLifecyclePayload(agent_id="m3", agent_type="model", members=["leaf", "x"]),
    )
    assert reg.apply_envelope(env, now=BASE)
    assert reg.get("m3").members == ["leaf", "x"]


def test_seed_agents_have_channels():
    reg = seed_default_registry(now=BASE)
    cat = reg.catalog_by_topic()
    assert "traffic-virtual-001" in cat[c.TrafficTopics.OBSERVATION]["producers"]
    assert "traffic-system-001" in cat[c.TrafficTopics.STATUS_INTERSECTION]["producers"]
    assert "traffic-system-001" in cat[c.TrafficTopics.OBSERVATION]["consumers"]
