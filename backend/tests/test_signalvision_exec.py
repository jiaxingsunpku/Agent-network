"""SignalVision 执行侧回归（P6 信号控制）：Safety Guard + 命令闭环 + SV 写端点映射。

按 docs/protocol.md §5 验证 handle_command 每个分支（纯逻辑，注入桩 client，无 Kafka/HTTP）。
运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anp import contracts as c
from anp.adapters.signalvision import (
    SV_EXEC_AGENT_ID,
    SignalVisionExecConfig,
    SignalVisionExecutor,
    SvResponse,
)

NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)
OK_PARAMS = {"desired_phase": "north_south_green", "duration_s": 25}
INTERSECTION = "gg-xiongchu-minzu"
SV_JUNCTION = "intersection_1_1"


class FakeSvClient:
    """记录 update_junction / 仿真启停调用、可配置成败的桩 SV 客户端。"""

    def __init__(
        self, *, update_ok: bool = True, status_ok: bool = True, sim_ok: bool = True, map_ok: bool = True
    ) -> None:
        self.update_ok = update_ok
        self.status_ok = status_ok
        self.sim_ok = sim_ok
        self.map_ok = map_ok
        self.calls: list[tuple[str, dict | None]] = []
        self.sim_calls: list[tuple[str, str | None]] = []  # (action, config)
        self.map_calls: list[str] = []  # 加载的 map_path

    def update_junction(self, junction_id, *, traffic_light=None, lane_data=None) -> SvResponse:
        self.calls.append((junction_id, traffic_light))
        if self.update_ok:
            return SvResponse(ok=True, status_code=200, body={"success": True})
        return SvResponse(ok=False, status_code=500, body={"success": False, "message": "boom"})

    def start_simulation(self, config) -> SvResponse:
        self.sim_calls.append(("start", config))
        if self.sim_ok:
            return SvResponse(ok=True, status_code=200, body={"success": True, "pid": 4321})
        return SvResponse(ok=False, status_code=200, body={"success": False, "message": "仿真已在运行中"})

    def stop_simulation(self) -> SvResponse:
        self.sim_calls.append(("stop", None))
        if self.sim_ok:
            return SvResponse(ok=True, status_code=200, body={"success": True})
        return SvResponse(ok=False, status_code=200, body={"success": False, "message": "没有正在运行的仿真"})

    def load_map(self, map_path) -> SvResponse:
        self.map_calls.append(map_path)
        if self.map_ok:
            return SvResponse(ok=True, status_code=200, body={"success": True, "junction_count": 9})
        return SvResponse(ok=False, status_code=404, body={"success": False, "message": "地图文件不存在"})

    def get_status(self) -> SvResponse:
        if self.status_ok:
            return SvResponse(ok=True, status_code=200, body={"running": True})
        return SvResponse(ok=False, status_code=None, body={"message": "SV down"})


def _executor(client: FakeSvClient, *, phase_state_map: dict | None = None) -> SignalVisionExecutor:
    cfg = SignalVisionExecConfig(
        junction_map={SV_JUNCTION: INTERSECTION},
        phase_state_map=phase_state_map or {},
    )
    return SignalVisionExecutor(cfg, client=client)


def _cmd(
    command_id: str,
    *,
    target: str = SV_EXEC_AGENT_ID,
    params=None,
    object_id: str | None = INTERSECTION,
    expires_at: str | None = None,
    command_type: c.CommandType = c.CommandType.SET_SIGNAL_PLAN,
) -> c.Envelope:
    default_params = OK_PARAMS if command_type == c.CommandType.SET_SIGNAL_PLAN else {"action": "start", "algorithm": "maxpressure"}
    return c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="traffic-gateway-001"),
        target_agent_id=target,
        payload=c.CommandPayload(
            command_id=command_id, command_type=command_type, params=params if params is not None else default_params
        ),
        expires_at=expires_at or c.expires_at_iso(30, from_ts=NOW),
        object_id=object_id,
    )


def _infer(command_id: str, *, params=None, object_id: str | None = INTERSECTION) -> c.Envelope:
    return _cmd(command_id, params=params, object_id=object_id, command_type=c.CommandType.CONTROL_SIGNAL_INFERENCE)


def _map(command_id: str, *, params=None, object_id: str | None = INTERSECTION) -> c.Envelope:
    return _cmd(command_id, params=params or {"map_path": "guanggu/netdata.pkl"}, object_id=object_id, command_type=c.CommandType.SET_SIGNAL_MAP)


def test_safety_guard_allow_and_reject():
    ex = _executor(FakeSvClient())
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


def test_completed_calls_sv_update_with_mapped_traffic_light():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_cmd("v1"), now=NOW)
    assert ack is not None and ack.status == c.AckStatus.COMPLETED
    assert ex.processed == 1
    # SV 写端点被调用一次，traffic_light 按单相位覆盖映射。
    assert len(fake.calls) == 1
    junction_id, tl = fake.calls[0]
    assert junction_id == SV_JUNCTION
    assert tl == {"phase_state": "north_south_green", "phase_duration": 0.0, "next_switch_time": 25.0}
    assert ex.applied_plan is not None and ex.applied_plan["intersection_id"] == INTERSECTION


def test_phase_state_map_overrides_phase_state():
    fake = FakeSvClient()
    ex = _executor(fake, phase_state_map={"north_south_green": "GGggrrrrGGggrrrr"})
    assert ex.handle_command(_cmd("p1"), now=NOW).status == c.AckStatus.COMPLETED
    assert fake.calls[0][1]["phase_state"] == "GGggrrrrGGggrrrr"


def test_duplicate_command():
    ex = _executor(FakeSvClient())
    assert ex.handle_command(_cmd("v1"), now=NOW).status == c.AckStatus.COMPLETED
    assert ex.handle_command(_cmd("v1"), now=NOW).status == c.AckStatus.DUPLICATE


def test_expired_command_not_executed():
    fake = FakeSvClient()
    ex = _executor(fake)
    expired_at = c.iso_utc(NOW - timedelta(seconds=5))
    ack = ex.handle_command(_cmd("e1", expires_at=expired_at), now=NOW)
    assert ack.status == c.AckStatus.EXPIRED
    assert fake.calls == []  # 未触达 SV


def test_rejected_by_safety_not_executed():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_cmd("r1", params={"desired_phase": "all_red", "duration_s": 999}), now=NOW)
    assert ack.status == c.AckStatus.REJECTED
    assert ack.safety is not None and ack.safety.allowed is False
    assert fake.calls == []
    assert ex.rejected == 1


def test_unknown_intersection_rejected():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_cmd("u1", object_id="not-mapped-intersection"), now=NOW)
    assert ack.status == c.AckStatus.REJECTED
    assert ack.safety is not None and "object_id" in (ack.safety.reason or "")
    assert fake.calls == []  # 路由失败不触达 SV


def test_missing_object_id_rejected():
    ex = _executor(FakeSvClient())
    ack = ex.handle_command(_cmd("m1", object_id=None), now=NOW)
    assert ack.status == c.AckStatus.REJECTED


def test_wrong_target_ignored():
    ex = _executor(FakeSvClient())
    ack = ex.handle_command(_cmd("w1", target="some-other-agent"), now=NOW)
    assert ack is None
    assert ex.ignored == 1


def test_non_command_ignored():
    ex = _executor(FakeSvClient())
    obs = c.observation_envelope(
        agent_id="x",
        payload=c.ObservationPayload(
            intersection_id="X",
            approaches=[c.Approach(direction=c.Direction.NORTH, vehicle_count=1, halting_count=1, mean_speed_mps=5.0)],
        ),
    )
    assert ex.handle_command(obs, now=NOW) is None


def test_sv_failure_yields_failed():
    fake = FakeSvClient(update_ok=False)
    ex = _executor(fake)
    ack = ex.handle_command(_cmd("f1"), now=NOW)
    assert ack.status == c.AckStatus.FAILED
    assert len(fake.calls) == 1  # 尝试过 SV，但失败
    assert ex.failed == 1


def test_dedup_blocks_reexecution_after_reject():
    ex = _executor(FakeSvClient())
    bad = _cmd("x1", params={"desired_phase": "all_red", "duration_s": 999})
    assert ex.handle_command(bad, now=NOW).status == c.AckStatus.REJECTED
    assert ex.handle_command(bad, now=NOW).status == c.AckStatus.DUPLICATE


def test_probe_sv_reachability():
    assert _executor(FakeSvClient(status_ok=True)).probe_sv() == (True, None)
    reachable, err = _executor(FakeSvClient(status_ok=False)).probe_sv()
    assert reachable is False and err


# --------------------------------------------------------------------------- #
# control_signal_inference（粗粒度：启停 / 选算法，真驱动 SUMO）
# --------------------------------------------------------------------------- #
def test_inference_start_completed_calls_sv_start():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i1", params={"action": "start", "algorithm": "maxpressure"}), now=NOW)
    assert ack is not None and ack.status == c.AckStatus.COMPLETED
    assert ex.processed == 1
    assert fake.sim_calls == [("start", "maxpressure")]
    assert fake.calls == []  # 不走 /update
    assert ex.applied_plan == {
        "action": "start",
        "algorithm": "maxpressure",
        "sv_response": {"success": True, "pid": 4321},
        "params": {"action": "start", "algorithm": "maxpressure"},
    }


def test_inference_stop_completed_calls_sv_stop():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i2", params={"action": "stop"}), now=NOW)
    assert ack.status == c.AckStatus.COMPLETED
    assert fake.sim_calls == [("stop", None)]


def test_inference_is_simulation_global_no_junction_required():
    """control_signal_inference 是仿真级（map 全局）操作，object_id 未映射也不拦（不走路由约束）。"""
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i3", object_id="not-mapped-or-irrelevant"), now=NOW)
    assert ack.status == c.AckStatus.COMPLETED
    assert fake.sim_calls == [("start", "maxpressure")]


def test_inference_safety_rejects_bad_action():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i4", params={"action": "pause"}), now=NOW)
    assert ack.status == c.AckStatus.REJECTED
    assert ack.safety is not None and "action" in (ack.safety.reason or "")
    assert fake.sim_calls == []  # 未触达 SV


def test_inference_safety_rejects_bad_algorithm():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i5", params={"action": "start", "algorithm": "skynet"}), now=NOW)
    assert ack.status == c.AckStatus.REJECTED
    assert ack.safety is not None and "algorithm" in (ack.safety.reason or "")
    assert fake.sim_calls == []


def test_inference_sv_failure_yields_failed():
    fake = FakeSvClient(sim_ok=False)
    ex = _executor(fake)
    ack = ex.handle_command(_infer("i6", params={"action": "start", "algorithm": "maxpressure"}), now=NOW)
    assert ack.status == c.AckStatus.FAILED
    assert fake.sim_calls == [("start", "maxpressure")]  # 尝试过 SV，但失败
    assert ex.failed == 1


def test_inference_safety_guard_direct():
    ex = _executor(FakeSvClient())
    ok = ex.safety_guard(c.CommandPayload(command_id="s1", command_type=c.CommandType.CONTROL_SIGNAL_INFERENCE, params={"action": "start", "algorithm": "colight"}))
    assert ok.allowed
    stop_ok = ex.safety_guard(c.CommandPayload(command_id="s2", command_type=c.CommandType.CONTROL_SIGNAL_INFERENCE, params={"action": "stop"}))
    assert stop_ok.allowed  # stop 不需要 algorithm


# --------------------------------------------------------------------------- #
# set_signal_map（切换路网：停仿真 + 加载地图）
# --------------------------------------------------------------------------- #
def test_map_completed_stops_sim_then_loads():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_map("m1", params={"map_path": "manhattan/netdata.pkl"}), now=NOW)
    assert ack is not None and ack.status == c.AckStatus.COMPLETED
    assert ex.processed == 1
    assert fake.sim_calls == [("stop", None)]      # 切图前先停仿真
    assert fake.map_calls == ["manhattan/netdata.pkl"]
    assert fake.calls == []                         # 不走 /update
    assert ex.applied_plan == {
        "action": "load_map",
        "map_path": "manhattan/netdata.pkl",
        "sv_response": {"success": True, "junction_count": 9},
        "params": {"map_path": "manhattan/netdata.pkl"},
    }


def test_map_is_simulation_global_no_junction_required():
    fake = FakeSvClient()
    ex = _executor(fake)
    ack = ex.handle_command(_map("m2", object_id="not-mapped"), now=NOW)
    assert ack.status == c.AckStatus.COMPLETED  # map 是 map 全局操作，不做 junction 路由


def test_map_safety_rejects_traversal_and_bad_suffix():
    fake = FakeSvClient()
    ex = _executor(fake)
    bad_traversal = ex.handle_command(_map("m3", params={"map_path": "../etc/passwd.pkl"}), now=NOW)
    assert bad_traversal.status == c.AckStatus.REJECTED and "穿越" in (bad_traversal.safety.reason or "")
    bad_suffix = ex.handle_command(_map("m4", params={"map_path": "guanggu/netdata.txt"}), now=NOW)
    assert bad_suffix.status == c.AckStatus.REJECTED and "结尾" in (bad_suffix.safety.reason or "")
    empty = ex.handle_command(_map("m5", params={"map_path": ""}), now=NOW)
    assert empty.status == c.AckStatus.REJECTED
    assert fake.map_calls == []  # 被拒不触达 SV


def test_map_sv_failure_yields_failed():
    fake = FakeSvClient(map_ok=False)
    ex = _executor(fake)
    ack = ex.handle_command(_map("m6", params={"map_path": "nope/netdata.pkl"}), now=NOW)
    assert ack.status == c.AckStatus.FAILED
    assert fake.map_calls == ["nope/netdata.pkl"]
    assert ex.failed == 1
