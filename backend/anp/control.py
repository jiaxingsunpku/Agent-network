"""下行控制命令的共享安全规则（`set_signal_plan` / `control_signal_inference`）。

权威 Safety Guard 在**各执行端本地**（docs/protocol.md §7），但其**规则**——合法相位集合、
时长区间、合法算法集合、参数形态校验——应当**单一来源**，避免不同执行体（v1 虚拟体、
SignalVision 执行 adapter）各自硬编码导致规则分叉（AGENTS.md §7.3）。

各执行端在本函数之上再叠加自己的设备/路由约束（如目标路口是否可达、是否映射到某 SV junction）。
本模块只做参数级判定，不含 IO、可单测。
"""

from __future__ import annotations

from .contracts import CommandPayload, CommandType, SafetyDecision

#: set_signal_plan 合法相位集合（沿用老前端语义档位）。
ALLOWED_SIGNAL_PHASES: tuple[str, ...] = ("north_south_green", "east_west_green", "all_red")
#: 单相位时长下限（秒）。
MIN_SIGNAL_DURATION_SEC = 5
#: 单相位时长上限（秒）。
MAX_SIGNAL_DURATION_SEC = 120

#: control_signal_inference 合法动作。
ALLOWED_SIGNAL_INFERENCE_ACTIONS: tuple[str, ...] = ("start", "stop")
#: control_signal_inference 合法信号控制算法（SignalVision 无 GUI 推理预设；`*_gui`/`*_db`
#: 变体不纳入，避免误起可视化/数据库重模式。action=start 时校验，stop 不需要 algorithm）。
ALLOWED_SIGNAL_ALGORITHMS: tuple[str, ...] = ("maxpressure", "colight", "fixedtime", "ppo")

#: set_signal_map 合法地图文件后缀（SV /api/load-map 仅支持 .pkl/.json）。
ALLOWED_SIGNAL_MAP_SUFFIXES: tuple[str, ...] = (".pkl", ".json")


def signal_plan_safety_decision(
    payload: CommandPayload,
    *,
    allowed_phases: tuple[str, ...] = ALLOWED_SIGNAL_PHASES,
    min_duration: float = MIN_SIGNAL_DURATION_SEC,
    max_duration: float = MAX_SIGNAL_DURATION_SEC,
) -> SafetyDecision:
    """`set_signal_plan` 的参数级 Safety Guard：命令类型白名单 + 相位/时长范围校验。

    通过 → ``allowed=True``；任一项不合规 → ``allowed=False`` 并附 ``reason``。
    """

    if payload.command_type != CommandType.SET_SIGNAL_PLAN:
        return SafetyDecision(
            allowed=False, decision="reject", reason=f"不支持的命令类型: {payload.command_type}"
        )
    params = payload.params or {}
    phase = params.get("desired_phase")
    if phase not in allowed_phases:
        return SafetyDecision(
            allowed=False,
            decision="reject",
            reason=f"desired_phase 非法（允许 {list(allowed_phases)}）: {phase!r}",
        )
    duration = params.get("duration_s")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        return SafetyDecision(allowed=False, decision="reject", reason="duration_s 必须是数值")
    if not (min_duration <= duration <= max_duration):
        return SafetyDecision(
            allowed=False,
            decision="reject",
            reason=f"duration_s 须在 [{min_duration}, {max_duration}]: {duration}",
        )
    return SafetyDecision(allowed=True, decision="allow", reason="通过本地 Safety Guard")


def signal_inference_safety_decision(
    payload: CommandPayload,
    *,
    allowed_actions: tuple[str, ...] = ALLOWED_SIGNAL_INFERENCE_ACTIONS,
    allowed_algorithms: tuple[str, ...] = ALLOWED_SIGNAL_ALGORITHMS,
) -> SafetyDecision:
    """`control_signal_inference` 的参数级 Safety Guard：命令类型白名单 + 动作/算法校验。

    params 形态 ``{action: "start"|"stop", algorithm: "maxpressure"|...}``；``action=start``
    须带合法 ``algorithm``，``action=stop`` 不校验 algorithm。通过 → ``allowed=True``。
    """

    if payload.command_type != CommandType.CONTROL_SIGNAL_INFERENCE:
        return SafetyDecision(
            allowed=False, decision="reject", reason=f"不支持的命令类型: {payload.command_type}"
        )
    params = payload.params or {}
    action = params.get("action")
    if action not in allowed_actions:
        return SafetyDecision(
            allowed=False,
            decision="reject",
            reason=f"action 非法（允许 {list(allowed_actions)}）: {action!r}",
        )
    if action == "start":
        algorithm = params.get("algorithm")
        if algorithm not in allowed_algorithms:
            return SafetyDecision(
                allowed=False,
                decision="reject",
                reason=f"algorithm 非法（允许 {list(allowed_algorithms)}）: {algorithm!r}",
            )
    return SafetyDecision(allowed=True, decision="allow", reason="通过本地 Safety Guard")


def signal_map_safety_decision(
    payload: CommandPayload,
    *,
    allowed_suffixes: tuple[str, ...] = ALLOWED_SIGNAL_MAP_SUFFIXES,
) -> SafetyDecision:
    """`set_signal_map` 的参数级 Safety Guard：命令类型白名单 + `map_path` 形态/防穿越校验。

    params 形态 ``{map_path: "<map>/netdata.pkl"}``（相对 SV map 目录）。拒绝绝对路径、`..`
    路径穿越、非 .pkl/.json 后缀；具体地图是否存在交 SV 校验（404）。
    """

    if payload.command_type != CommandType.SET_SIGNAL_MAP:
        return SafetyDecision(
            allowed=False, decision="reject", reason=f"不支持的命令类型: {payload.command_type}"
        )
    params = payload.params or {}
    map_path = params.get("map_path")
    if not isinstance(map_path, str) or not map_path.strip():
        return SafetyDecision(allowed=False, decision="reject", reason="map_path 必填且为非空字符串")
    mp = map_path.strip()
    if mp.startswith("/") or mp.startswith("\\") or ".." in mp:
        return SafetyDecision(
            allowed=False, decision="reject", reason=f"map_path 非法（禁止绝对路径/路径穿越）: {mp!r}"
        )
    if not mp.endswith(allowed_suffixes):
        return SafetyDecision(
            allowed=False,
            decision="reject",
            reason=f"map_path 须以 {list(allowed_suffixes)} 结尾: {mp!r}",
        )
    return SafetyDecision(allowed=True, decision="allow", reason="通过本地 Safety Guard")
