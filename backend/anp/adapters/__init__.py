"""感知接入适配器（adapters）—— 把外部真实数据源桥接到统一契约的观测。

每个外部数据源（如 SignalVision 智能交通推理系统）一个子包，职责单一：
**把数据源的原生结构映射成 ``anp.contracts`` 的按方向原始观测，经统一 envelope
builder 发布到感知层 topic**。适配器只做接入与结构映射，**不在此散搓 envelope、
不计算 World Status**（共识指标一律由系统级智能体算，AGENTS.md §3.2）。

设计与映射契约见 docs/adapters.md。
"""

from __future__ import annotations

from .signalvision import (
    SignalVisionAdapter,
    SignalVisionAdapterConfig,
    SignalVisionClient,
    map_junction_to_observation,
)

__all__ = [
    "SignalVisionAdapter",
    "SignalVisionAdapterConfig",
    "SignalVisionClient",
    "map_junction_to_observation",
]
