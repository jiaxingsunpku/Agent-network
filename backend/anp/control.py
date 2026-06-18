"""下行控制命令的共享安全规则（`set_signal_plan`）。

权威 Safety Guard 在**各执行端本地**（docs/protocol.md §7），但其**规则**——合法相位集合、
时长区间、参数形态校验——应当**单一来源**，避免不同执行体（v1 虚拟体、SignalVision 执行
adapter）各自硬编码导致规则分叉（AGENTS.md §7.3）。

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
