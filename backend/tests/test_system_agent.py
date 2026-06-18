"""系统级智能体回归测试：窗口聚合、指标公式、拥堵档位、过滤、全管道（无 Kafka）。

运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anp import contracts as c
from anp.system_agent import (
    GRACE_SEC,
    MIN_CONFIDENCE,
    VEH_SPACING_M,
    V_FREE_KMH,
    WINDOW_SIZE_SEC,
    SystemAgent,
    WindowAggregator,
    compute_status,
)
from anp.system_agent.compute import _classify, _derive_delay_from_speed
from anp.system_agent.windowing import ClosedWindow

BASE = datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone.utc)


def _at(sec: float) -> datetime:
    return BASE + timedelta(seconds=sec)


def _obs(intersection: str, **dirs: tuple[int, int, float]) -> c.ObservationPayload:
    """dirs: direction_name -> (vehicle_count, halting_count, mean_speed_mps)。"""

    approaches = [
        c.Approach(direction=c.Direction(name), vehicle_count=v, halting_count=h, mean_speed_mps=s)
        for name, (v, h, s) in dirs.items()
    ]
    return c.ObservationPayload(intersection_id=intersection, approaches=approaches)


# --------------------------------------------------------------------------- #
# 常量与文档一致（docs/world-status.md §1/§4）
# --------------------------------------------------------------------------- #
def test_constants_match_doc():
    assert WINDOW_SIZE_SEC == 10
    assert GRACE_SEC == 2
    assert MIN_CONFIDENCE == 0.3
    assert VEH_SPACING_M == 7.0
    assert V_FREE_KMH == 40.0


# --------------------------------------------------------------------------- #
# 窗口聚合：切桶、grace 关窗、迟到丢弃
# --------------------------------------------------------------------------- #
def test_window_closes_on_watermark_after_grace():
    agg = WindowAggregator()
    # 窗口 [0,10) 内 5 拍，水位最高 8 < 12，不关窗。
    for t in (0, 2, 4, 6, 8):
        closed = agg.add("X", _at(t), _obs("X", north=(5, 3, 8.0)))
        assert closed == []
    # t=12 进入 [10,20)，水位 12 ≥ [0,10) 的 close_at(12) → 结算 [0,10)。
    closed = agg.add("X", _at(12), _obs("X", north=(5, 3, 8.0)))
    assert len(closed) == 1
    w = closed[0]
    assert w.intersection_id == "X"
    assert w.start == _at(0) and w.end == _at(10)
    assert w.sample_count == 5


def test_late_message_after_close_is_dropped():
    agg = WindowAggregator()
    for t in (0, 2, 4, 6, 8):
        agg.add("X", _at(t), _obs("X", north=(5, 3, 8.0)))
    agg.add("X", _at(12), _obs("X", north=(5, 3, 8.0)))  # 关 [0,10)
    # 迟到一条 event_ts=5（落在已关闭的 [0,10)）→ 丢弃。
    closed = agg.add("X", _at(5), _obs("X", north=(5, 3, 8.0)))
    assert closed == []
    assert agg.dropped_late == 1


def test_flush_all_settles_remaining():
    agg = WindowAggregator()
    for t in (10, 12, 14):
        agg.add("X", _at(t), _obs("X", north=(5, 3, 8.0)))
    # 未达水位，主动 flush。
    remaining = agg.flush_all()
    assert len(remaining) == 1
    assert remaining[0].sample_count == 3


def test_windows_are_per_intersection():
    agg = WindowAggregator()
    agg.add("A", _at(0), _obs("A", north=(5, 3, 8.0)))
    agg.add("B", _at(0), _obs("B", south=(5, 3, 8.0)))
    # A 的水位推进不应关 B 的窗口。
    closed_a = agg.add("A", _at(12), _obs("A", north=(5, 3, 8.0)))
    assert len(closed_a) == 1 and closed_a[0].intersection_id == "A"
    settled = {w.intersection_id for w in agg.flush_all()}
    assert settled == {"A", "B"}


# --------------------------------------------------------------------------- #
# 指标计算：精确公式（docs/world-status.md §4）
# --------------------------------------------------------------------------- #
def test_compute_status_exact_formulas():
    window = ClosedWindow(
        intersection_id="X",
        start=_at(0),
        end=_at(10),
        observations=[
            _obs("X", north=(10, 4, 8.0), south=(6, 2, 10.0)),
            _obs("X", north=(10, 6, 6.0), south=(4, 4, 8.0)),
        ],
    )
    st = compute_status(window)

    # 排队：north mean(4,6)=5×7=35；south mean(2,4)=3×7=21；合计 56。
    assert st.queue_length_m == pytest.approx(56.0)
    # 流量：north 20/10*3600=7200；south 10/10*3600=3600；合计 10800。
    assert st.flow_veh_h == pytest.approx(10800.0)
    # 速度：north 加权 (8*10+6*10)/20=7.0→25.2km/h；south (10*6+8*4)/10=9.2→33.12；
    #       路口按方向通过量加权 (25.2*20+33.12*10)/30=27.84。
    assert st.mean_speed_kmh == pytest.approx(27.84)

    north = next(a for a in st.approaches if a.direction == c.Direction.NORTH)
    assert north.queue_length_m == pytest.approx(35.0)
    assert north.flow_veh_h == pytest.approx(7200.0)
    assert north.mean_speed_kmh == pytest.approx(25.2)

    # 延误（无观测延误→由速度推导）：north 200/7-18=10.5714；south 200/9.2-18=3.7391；
    #       加权 (10.5714*20+3.7391*10)/30≈8.294 → 畅通。
    assert st.mean_delay_sec == pytest.approx(8.294, abs=1e-2)
    assert st.congestion_level == c.CongestionLevel.SMOOTH
    assert st.congestion_index == pytest.approx(8.294 / 60, abs=1e-3)
    assert st.window.sample_count == 2 and st.window.size_sec == 10


def test_compute_uses_observed_delay_when_present():
    # 带观测延误时取（按通过量）加权均值，不走速度推导。
    window = ClosedWindow(
        intersection_id="X",
        start=_at(0),
        end=_at(10),
        observations=[
            c.ObservationPayload(
                intersection_id="X",
                approaches=[
                    c.Approach(direction=c.Direction.NORTH, vehicle_count=10, halting_count=5,
                               mean_speed_mps=5.0, mean_delay_sec=60.0)
                ],
            )
        ],
    )
    st = compute_status(window)
    assert st.mean_delay_sec == pytest.approx(60.0)
    assert st.congestion_level == c.CongestionLevel.SEVERE


def test_derive_delay_and_classify_tiers():
    v_free = V_FREE_KMH / 3.6
    assert _derive_delay_from_speed(v_free) == pytest.approx(0.0)  # 自由流 → 0 延误
    assert _classify(27.0) == c.CongestionLevel.SMOOTH
    assert _classify(38.0) == c.CongestionLevel.SLOW
    assert _classify(52.0) == c.CongestionLevel.CONGESTED
    assert _classify(52.01) == c.CongestionLevel.SEVERE


def test_zero_vehicles_falls_back_to_unweighted_speed():
    window = ClosedWindow(
        intersection_id="X", start=_at(0), end=_at(10),
        observations=[_obs("X", north=(0, 2, 4.0)), _obs("X", north=(0, 2, 6.0))],
    )
    st = compute_status(window)
    # 通过量为 0，速度退化为算术平均 (4+6)/2=5.0 → 18km/h。
    assert st.mean_speed_kmh == pytest.approx(18.0)
    assert st.flow_veh_h == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# 服务层：过滤 + 全管道（producer=None，无 Kafka）
# --------------------------------------------------------------------------- #
def _obs_env(intersection: str, sec: float, *, confidence: float = 1.0) -> c.Envelope:
    return c.observation_envelope(
        agent_id="traffic-virtual-001",
        payload=_obs(intersection, north=(8, 5, 6.0), south=(6, 3, 9.0)),
        event_ts=c.iso_utc(_at(sec)),
        quality=c.Quality(confidence=confidence),
    )


def test_low_confidence_dropped():
    agent = SystemAgent(producer=None)
    out = agent.feed_envelope(_obs_env("X", 0, confidence=0.1))
    assert out == []
    assert agent.dropped_confidence == 1
    assert agent.accepted == 0


def test_non_observation_ignored():
    agent = SystemAgent(producer=None)
    cmd = c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="g"),
        target_agent_id="traffic-virtual-001",
        payload=c.CommandPayload(command_id="1", command_type=c.CommandType.SET_SIGNAL_PLAN, params={}),
        expires_at=c.expires_at_iso(30),
    )
    assert agent.feed_envelope(cmd) == []
    assert agent.accepted == 0 and agent.dropped_confidence == 0


def test_full_pipeline_without_kafka():
    agent = SystemAgent(producer=None)
    emitted: list = []
    for t in (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22):
        emitted += agent.feed_envelope(_obs_env("X", t))
    # [0,10) 与 [10,20) 由水位关闭。
    assert agent.windows_emitted == 2
    emitted += agent.flush()  # 结算 [20,30)
    assert agent.windows_emitted == 3
    assert agent.accepted == 12
    # 内存当前态指向最后一个窗口。
    latest = agent.store.get("X")
    assert latest is not None
    assert latest.window.end == c.iso_utc(_at(30))
    assert len(agent.store) == 1
