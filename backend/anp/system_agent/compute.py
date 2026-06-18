"""窗口 → 路口 World Status 的指标计算 —— 纯函数，严格按 docs/world-status.md §4。

输入一个已关闭窗口（含窗口内各方向多次采样），输出一条 IntersectionStatusPayload。
不碰 Kafka、不维护状态，便于单测对齐公式。
"""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import (
    ApproachStatus,
    CongestionLevel,
    Direction,
    IntersectionStatusPayload,
    StatusWindow,
    iso_utc,
)
from .constants import (
    CONGESTION_INDEX_DENOM,
    DELAY_CONGESTED_MAX,
    DELAY_SLOW_MAX,
    DELAY_SMOOTH_MAX,
    MAX_DERIVED_DELAY_SEC,
    MIN_SPEED_MPS,
    MPS_TO_KMH,
    SECONDS_PER_HOUR,
    SEGMENT_LEN_M,
    V_FREE_KMH,
    VEH_SPACING_M,
    WINDOW_SIZE_SEC,
)
from .windowing import ClosedWindow

#: 输出方向的稳定排序（北、南、东、西）。
_DIR_ORDER = {d: i for i, d in enumerate(Direction)}


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    """按权重求均值；权重之和为 0 时退化为算术平均；无样本时返回 0。"""

    if not values:
        return 0.0
    total_w = sum(weights)
    if total_w > 0:
        return sum(v * w for v, w in zip(values, weights)) / total_w
    return sum(values) / len(values)


def _derive_delay_from_speed(speed_mps: float) -> float:
    """由观测速度推导延误（秒）：名义段长行程时间差，见 world-status.md §4。

    delay ≈ max(0, L/v_obs − L/v_free)，对极低速做下限保护并封顶。
    """

    v_obs = max(speed_mps, MIN_SPEED_MPS)
    v_free = V_FREE_KMH / MPS_TO_KMH
    delay = SEGMENT_LEN_M / v_obs - SEGMENT_LEN_M / v_free
    return min(max(delay, 0.0), MAX_DERIVED_DELAY_SEC)


def _classify(mean_delay_sec: float) -> CongestionLevel:
    """按 mean_delay_sec 映射拥堵等级（world-status.md §4 档位）。"""

    if mean_delay_sec <= DELAY_SMOOTH_MAX:
        return CongestionLevel.SMOOTH
    if mean_delay_sec <= DELAY_SLOW_MAX:
        return CongestionLevel.SLOW
    if mean_delay_sec <= DELAY_CONGESTED_MAX:
        return CongestionLevel.CONGESTED
    return CongestionLevel.SEVERE


class _DirAccum:
    """单方向窗口内累计：滞留序列、通过量、速度（带权）、延误（带权，仅观测带值时）。"""

    __slots__ = ("halting", "vehicles", "speeds", "speed_w", "delays", "delay_w")

    def __init__(self) -> None:
        self.halting: list[int] = []
        self.vehicles = 0
        self.speeds: list[float] = []
        self.speed_w: list[float] = []
        self.delays: list[float] = []
        self.delay_w: list[float] = []

    def queue_length_m(self) -> float:
        # 各方向滞留窗口均值 × 车均占用长度。
        mean_halting = sum(self.halting) / len(self.halting) if self.halting else 0.0
        return mean_halting * VEH_SPACING_M

    def flow_veh_h(self) -> float:
        return self.vehicles / WINDOW_SIZE_SEC * SECONDS_PER_HOUR

    def speed_mps(self) -> float:
        return _weighted_mean(self.speeds, self.speed_w)

    def delay_sec(self) -> float:
        # 观测带延误 → 加权均值；否则由方向速度推导。
        if self.delays:
            return _weighted_mean(self.delays, self.delay_w)
        return _derive_delay_from_speed(self.speed_mps())


def compute_status(window: ClosedWindow) -> IntersectionStatusPayload:
    """把一个已关闭窗口聚合为路口 World Status payload。"""

    per_dir: dict[Direction, _DirAccum] = {}
    for obs in window.observations:
        for ap in obs.approaches:
            acc = per_dir.setdefault(ap.direction, _DirAccum())
            acc.halting.append(ap.halting_count)
            acc.vehicles += ap.vehicle_count
            acc.speeds.append(ap.mean_speed_mps)
            acc.speed_w.append(float(ap.vehicle_count))
            if ap.mean_delay_sec is not None:
                acc.delays.append(ap.mean_delay_sec)
                acc.delay_w.append(float(ap.vehicle_count))

    approaches: list[ApproachStatus] = []
    queue_total = 0.0
    flow_total = 0.0
    # 路口级速度/延误按方向通过量加权聚合。
    dir_speeds_kmh: list[float] = []
    dir_speed_w: list[float] = []
    dir_delays: list[float] = []
    dir_delay_w: list[float] = []

    for direction in sorted(per_dir, key=lambda d: _DIR_ORDER[d]):
        acc = per_dir[direction]
        q = acc.queue_length_m()
        f = acc.flow_veh_h()
        spd_kmh = acc.speed_mps() * MPS_TO_KMH
        approaches.append(
            ApproachStatus(direction=direction, queue_length_m=q, flow_veh_h=f, mean_speed_kmh=spd_kmh)
        )
        queue_total += q
        flow_total += f
        w = float(acc.vehicles)
        dir_speeds_kmh.append(spd_kmh)
        dir_speed_w.append(w)
        dir_delays.append(acc.delay_sec())
        dir_delay_w.append(w)

    mean_speed_kmh = _weighted_mean(dir_speeds_kmh, dir_speed_w)
    mean_delay_sec = _weighted_mean(dir_delays, dir_delay_w)
    congestion_index = min(max(mean_delay_sec / CONGESTION_INDEX_DENOM, 0.0), 1.0)

    return IntersectionStatusPayload(
        intersection_id=window.intersection_id,
        window=StatusWindow(
            start=iso_utc(window.start),
            end=iso_utc(window.end),
            size_sec=WINDOW_SIZE_SEC,
            sample_count=window.sample_count,
        ),
        queue_length_m=queue_total,
        flow_veh_h=flow_total,
        mean_speed_kmh=mean_speed_kmh,
        mean_delay_sec=mean_delay_sec,
        congestion_level=_classify(mean_delay_sec),
        congestion_index=congestion_index,
        approaches=approaches,
    )
