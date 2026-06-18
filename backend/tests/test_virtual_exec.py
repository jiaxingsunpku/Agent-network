"""虚拟体执行端回归：本地 Safety Guard + 命令闭环（去重/过期/目标/执行/ack）。

按 docs/protocol.md §5 验证 handle_command 的每个分支（纯逻辑，无 Kafka）。
运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.virtual_traffic import VIRTUAL_AGENT_ID, VirtualTrafficExecutor
from anp import contracts as c

NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)
OK_PARAMS = {"desired_phase": "north_south_green", "duration_s": 25}


def _cmd(command_id: str, *, target: str = VIRTUAL_AGENT_ID, params=None, expires_at: str | None = None) -> c.Envelope:
    return c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="traffic-gateway-001"),
        target_agent_id=target,
        payload=c.CommandPayload(
            command_id=command_id, command_type=c.CommandType.SET_SIGNAL_PLAN, params=params or OK_PARAMS
        ),
        expires_at=expires_at or c.expires_at_iso(30, from_ts=NOW),
    )


def test_safety_guard_allow_and_reject():
    ex = VirtualTrafficExecutor()
    ok = ex.safety_guard(c.CommandPayload(command_id="1", command_type=c.CommandType.SET_SIGNAL_PLAN, params=OK_PARAMS))
    assert ok.allowed
    bad_phase = ex.safety_guard(
        c.CommandPayload(command_id="2", command_type=c.CommandType.SET_SIGNAL_PLAN, params={"desired_phase": "diagonal", "duration_s": 25})
    )
    assert not bad_phase.allowed and "desired_phase" in (bad_phase.reason or "")
    bad_dur = ex.safety_guard(
        c.CommandPayload(command_id="3", command_type=c.CommandType.SET_SIGNAL_PLAN, params={"desired_phase": "all_red", "duration_s": 999})
    )
    assert not bad_dur.allowed and "duration_s" in (bad_dur.reason or "")


def test_handle_valid_command_completed():
    ex = VirtualTrafficExecutor()
    ack = ex.handle_command(_cmd("v1"), now=NOW)
    assert ack is not None and ack.status == c.AckStatus.COMPLETED
    assert ex.applied_plan == OK_PARAMS
    assert ex.processed == 1


def test_handle_duplicate_command():
    ex = VirtualTrafficExecutor()
    assert ex.handle_command(_cmd("v1"), now=NOW).status == c.AckStatus.COMPLETED
    dup = ex.handle_command(_cmd("v1"), now=NOW)
    assert dup.status == c.AckStatus.DUPLICATE


def test_handle_expired_command():
    ex = VirtualTrafficExecutor()
    expired_at = c.iso_utc(NOW - timedelta(seconds=5))
    ack = ex.handle_command(_cmd("e1", expires_at=expired_at), now=NOW)
    assert ack.status == c.AckStatus.EXPIRED
    assert ex.applied_plan is None  # 未执行


def test_handle_rejected_by_safety():
    ex = VirtualTrafficExecutor()
    ack = ex.handle_command(_cmd("r1", params={"desired_phase": "all_red", "duration_s": 999}), now=NOW)
    assert ack.status == c.AckStatus.REJECTED
    assert ack.safety is not None and ack.safety.allowed is False


def test_handle_wrong_target_ignored():
    ex = VirtualTrafficExecutor()
    ack = ex.handle_command(_cmd("w1", target="some-other-agent"), now=NOW)
    assert ack is None
    assert ex.ignored == 1


def test_handle_non_command_ignored():
    ex = VirtualTrafficExecutor()
    obs = c.observation_envelope(
        agent_id="traffic-virtual-001",
        payload=c.ObservationPayload(
            intersection_id="X",
            approaches=[c.Approach(direction=c.Direction.NORTH, vehicle_count=1, halting_count=1, mean_speed_mps=5.0)],
        ),
    )
    assert ex.handle_command(obs, now=NOW) is None


def test_dedup_blocks_reexecution_after_reject():
    # 越界命令被 rejected 后进入去重表；同 id 再来 → duplicate（不重复评估）。
    ex = VirtualTrafficExecutor()
    bad = _cmd("x1", params={"desired_phase": "all_red", "duration_s": 999})
    assert ex.handle_command(bad, now=NOW).status == c.AckStatus.REJECTED
    assert ex.handle_command(bad, now=NOW).status == c.AckStatus.DUPLICATE
