"""系统级智能体 ``traffic-system-001``（docs/architecture.md §4/§5、world-status.md）。

消费感知层观测 → 按路口 10s 滚动窗口（event_ts，grace 2s）聚合/清洗/过滤 →
计算路口 World Status → 写状态层 topic + 内存维护每路口最新态。常量集中在
:mod:`.constants`，与 docs/world-status.md 保持一致。
"""

from __future__ import annotations

from .compute import compute_status
from .constants import (
    GRACE_SEC,
    MIN_CONFIDENCE,
    SYSTEM_AGENT_ID,
    VEH_SPACING_M,
    V_FREE_KMH,
    WINDOW_SIZE_SEC,
)
from .service import SystemAgent, build_default_agent
from .state import LatestStatusStore
from .windowing import ClosedWindow, WindowAggregator

__all__ = [
    "SystemAgent",
    "build_default_agent",
    "LatestStatusStore",
    "WindowAggregator",
    "ClosedWindow",
    "compute_status",
    "SYSTEM_AGENT_ID",
    "WINDOW_SIZE_SEC",
    "GRACE_SEC",
    "MIN_CONFIDENCE",
    "VEH_SPACING_M",
    "V_FREE_KMH",
]
