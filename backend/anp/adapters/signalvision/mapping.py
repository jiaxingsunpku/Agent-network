"""SV junction 状态 → 统一契约按方向观测的纯映射逻辑。

为什么需要映射（语义鸿沟，详见 docs/adapters.md）：

1. **方向**：SV 的 lane `direction` 只有 ``incoming`` / ``outgoing``，没有罗盘方向；
   而契约 :class:`Approach` 要求 ``direction ∈ {north,south,east,west}``。本模块把
   **进口（incoming）车道**按策略归并成至多 4 个罗盘方向 approach（lane_id 罗盘
   token 优先，抽不到按序号轮询 N/S/E/W）。
2. **通过量**：契约 ``vehicle_count`` 是「采样间隔内**通过**量」（吞吐），而 SV 的
   lane ``vehicle_count`` 是**瞬时**在道车数。直接塞瞬时数会让下游 ``flow_veh_h``
   虚高一个量级。本模块用 junction ``metrics.total_vehicles_passed``（累计通过量）
   **轮询差分**得到本间隔通过量，再按各方向瞬时车数占比整数分摊。
3. **延误**：``mean_delay_sec`` 留空，交系统级智能体按速度推导——adapter 不算 World
   Status（AGENTS.md §3.2）。

纯函数、无 IO、无 Kafka，便于单测。
"""

from __future__ import annotations

import re

from anp.contracts import Approach, Direction, ObservationPayload

from .config import DIRECTION_STRATEGY_ROUND_ROBIN

#: 轮询兜底用的固定方向次序。
_RR_DIRECTIONS: tuple[Direction, ...] = (
    Direction.NORTH,
    Direction.SOUTH,
    Direction.EAST,
    Direction.WEST,
)

#: lane_id 罗盘 token 模式（英文整词、中文、边界单字母），按方向分组。
_TOKEN_PATTERNS: dict[Direction, tuple[re.Pattern[str], ...]] = {
    Direction.NORTH: (re.compile(r"north"), re.compile(r"北"), re.compile(r"(?<![a-z])n(?![a-z])")),
    Direction.SOUTH: (re.compile(r"south"), re.compile(r"南"), re.compile(r"(?<![a-z])s(?![a-z])")),
    Direction.EAST: (re.compile(r"east"), re.compile(r"东"), re.compile(r"(?<![a-z])e(?![a-z])")),
    Direction.WEST: (re.compile(r"west"), re.compile(r"西"), re.compile(r"(?<![a-z])w(?![a-z])")),
}


# --------------------------------------------------------------------------- #
# 小工具：稳健取数
# --------------------------------------------------------------------------- #
def _as_int(value: object, default: int = 0) -> int:
    try:
        return max(int(value), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return f if f >= 0 else default


def _token_direction(lane_id: str) -> Direction | None:
    """从 lane_id 抽罗盘方向；抽不到返回 None。整词/中文优先于单字母。"""

    low = lane_id.lower()
    # 先匹配整词与中文（更可靠），再回退单字母，避免 "n" 误命中含 n 的词。
    for pass_idx in (0, 1, 2):
        for direction, patterns in _TOKEN_PATTERNS.items():
            if patterns[pass_idx].search(low):
                return direction
    return None


def resolve_direction(lane_id: str, index: int, strategy: str) -> Direction:
    """决定一条进口车道归属的罗盘方向。

    - ``round_robin``：一律按排序序号轮询。
    - 其余（``auto``）：先试 lane_id 罗盘 token，抽不到再轮询。
    """

    if strategy == DIRECTION_STRATEGY_ROUND_ROBIN:
        return _RR_DIRECTIONS[index % len(_RR_DIRECTIONS)]
    return _token_direction(lane_id) or _RR_DIRECTIONS[index % len(_RR_DIRECTIONS)]


def _distribute_int(total: int, weights: list[int]) -> list[int]:
    """把整数 ``total`` 按 ``weights`` 占比分摊为整数列表，和恰为 total（最大余数法）。

    权重全 0（无瞬时车数）时平均分摊。``total<=0`` 时全 0。
    """

    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    wsum = sum(weights)
    if wsum <= 0:  # 无瞬时车数 → 均分
        base, rem = divmod(total, n)
        return [base + (1 if i < rem else 0) for i in range(n)]
    raw = [total * w / wsum for w in weights]
    floors = [int(x) for x in raw]
    rem = total - sum(floors)
    # 余数按小数部分从大到小补 1。
    order = sorted(range(n), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:rem]:
        floors[i] += 1
    return floors


def _group_incoming(incoming_lanes: dict, strategy: str) -> dict[Direction, list[dict]]:
    """把进口车道按策略归并到罗盘方向（按 lane_id 排序保证序号稳定）。"""

    groups: dict[Direction, list[dict]] = {}
    for index, (lane_id, lane) in enumerate(sorted(incoming_lanes.items())):
        lane_dict = lane if isinstance(lane, dict) else {}
        direction = resolve_direction(str(lane_id), index, strategy)
        groups.setdefault(direction, []).append(lane_dict)
    return groups


def throughput_delta(passed_now: int, prev_passed: int | None) -> int:
    """由累计通过量推本间隔通过量。

    首轮（``prev_passed is None``）无基线 → 0；计数器回退（SV 重启）视为从 0 重计，
    取 ``passed_now``。
    """

    if prev_passed is None:
        return 0
    if passed_now >= prev_passed:
        return passed_now - prev_passed
    return max(passed_now, 0)


def map_junction_to_observation(
    junction: dict,
    intersection_id: str,
    *,
    prev_passed: int | None,
    strategy: str,
) -> tuple[ObservationPayload | None, int]:
    """把一个 SV junction 状态字典映射成按方向观测。

    入参 ``junction`` 是 SV ``/api/junctions/<id>`` 返回的 junction 状态字典
    （``incoming_lanes`` / ``metrics`` / ``traffic_light``）。

    返回 ``(payload | None, passed_now)``：
      - ``payload``：归并后的 :class:`ObservationPayload`；无进口车道时为 ``None``（跳过）。
      - ``passed_now``：本轮累计通过量，调用方据此更新 ``prev_passed``。

    每方向：
      - ``halting_count`` = 该方向各进口车道瞬时滞留之和；
      - ``mean_speed_mps`` = 各车道速度按瞬时车数加权均值（无车数则算术均值）；
      - ``vehicle_count`` = junction 间隔通过量按方向瞬时车数占比整数分摊。
      - ``mean_delay_sec`` 留空（系统级推导）。
    """

    incoming = junction.get("incoming_lanes") if isinstance(junction.get("incoming_lanes"), dict) else {}
    metrics = junction.get("metrics") if isinstance(junction.get("metrics"), dict) else {}
    passed_now = _as_int(metrics.get("total_vehicles_passed"))

    if not incoming:
        return None, passed_now

    groups = _group_incoming(incoming, strategy)
    # 稳定方向次序（固定 N/S/E/W）方便分摊与可读。
    directions = [d for d in _RR_DIRECTIONS if d in groups]

    inst_vehicles: list[int] = []
    halting: list[int] = []
    speeds: list[float] = []
    for d in directions:
        lanes = groups[d]
        veh = sum(_as_int(l.get("vehicle_count")) for l in lanes)
        halt = sum(_as_int(l.get("halting_count")) for l in lanes)
        if veh > 0:
            speed = sum(_as_float(l.get("mean_speed")) * _as_int(l.get("vehicle_count")) for l in lanes) / veh
        elif lanes:
            speed = sum(_as_float(l.get("mean_speed")) for l in lanes) / len(lanes)
        else:  # pragma: no cover - directions 只含非空组
            speed = 0.0
        inst_vehicles.append(veh)
        halting.append(halt)
        speeds.append(speed)

    delta = throughput_delta(passed_now, prev_passed)
    flow = _distribute_int(delta, inst_vehicles)

    approaches = [
        Approach(
            direction=directions[i],
            vehicle_count=flow[i],
            halting_count=halting[i],
            mean_speed_mps=round(speeds[i], 2),
        )
        for i in range(len(directions))
    ]
    payload = ObservationPayload(intersection_id=intersection_id, approaches=approaches)
    return payload, passed_now
