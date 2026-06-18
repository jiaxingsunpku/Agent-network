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
