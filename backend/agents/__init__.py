"""可独立运行的示例智能体（docs/architecture.md §4）。

v1：虚拟交通智能体（感知 + 执行二合一，``traffic-virtual-001``）。本期实现感知上报，
执行（接命令 + 本地 Safety Guard）留到 P3 命令闭环。
"""

from __future__ import annotations

from .virtual_traffic import VIRTUAL_AGENT_ID, VirtualTrafficAgent

__all__ = ["VirtualTrafficAgent", "VIRTUAL_AGENT_ID"]
