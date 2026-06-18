"""SignalVision 感知接入适配器子包。

对外导出：配置、HTTP 客户端、纯映射函数、服务编排。映射契约与设计决策见
docs/adapters.md。本子包**只做感知接入**（命令控制闭环属后续「信号控制」任务）。
"""

from __future__ import annotations

from .client import SignalVisionClient, SvResponse
from .config import (
    DEFAULT_JUNCTION_MAP,
    DIRECTION_STRATEGY_AUTO,
    DIRECTION_STRATEGY_ROUND_ROBIN,
    SV_ADAPTER_AGENT_ID,
    SV_ADAPTER_AGENT_TYPE,
    SignalVisionAdapterConfig,
)
from .mapping import (
    map_junction_to_observation,
    resolve_direction,
    throughput_delta,
)
from .service import (
    SignalVisionAdapter,
    PollResult,
    heartbeat_envelope,
    lifecycle_envelope,
)

__all__ = [
    # config
    "SignalVisionAdapterConfig",
    "SV_ADAPTER_AGENT_ID",
    "SV_ADAPTER_AGENT_TYPE",
    "DEFAULT_JUNCTION_MAP",
    "DIRECTION_STRATEGY_AUTO",
    "DIRECTION_STRATEGY_ROUND_ROBIN",
    # client
    "SignalVisionClient",
    "SvResponse",
    # mapping
    "map_junction_to_observation",
    "resolve_direction",
    "throughput_delta",
    # service
    "SignalVisionAdapter",
    "PollResult",
    "lifecycle_envelope",
    "heartbeat_envelope",
]
