"""SignalVision 接入适配器子包。

对外导出：配置、HTTP 客户端、纯映射函数、感知服务编排、信号控制执行器。映射契约与
设计决策见 docs/adapters.md。**感知侧**（observation）+ **执行侧**（P6 信号控制：
set_signal_plan → SV traffic_light）同子包、职责分离。
"""

from __future__ import annotations

from .client import SignalVisionClient, SvResponse
from .config import (
    DEFAULT_JUNCTION_MAP,
    DIRECTION_STRATEGY_AUTO,
    DIRECTION_STRATEGY_ROUND_ROBIN,
    SV_ADAPTER_AGENT_ID,
    SV_ADAPTER_AGENT_TYPE,
    SV_EXEC_AGENT_ID,
    SignalVisionAdapterConfig,
    SignalVisionExecConfig,
)
from .executor import (
    EXEC_CAPABILITIES,
    EXEC_COMMAND_TYPES,
    SignalVisionExecutor,
    exec_heartbeat_envelope,
    exec_lifecycle_envelope,
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
from .world import (
    build_signalvision_adapter_world_client,
    build_signalvision_exec_world_client,
    signalvision_adapter_produces,
    signalvision_executor_consumes,
    signalvision_executor_produces,
)

__all__ = [
    # config
    "SignalVisionAdapterConfig",
    "SignalVisionExecConfig",
    "SV_ADAPTER_AGENT_ID",
    "SV_ADAPTER_AGENT_TYPE",
    "SV_EXEC_AGENT_ID",
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
    # service（感知侧）
    "SignalVisionAdapter",
    "PollResult",
    "lifecycle_envelope",
    "heartbeat_envelope",
    # executor（执行侧 / 信号控制）
    "SignalVisionExecutor",
    "EXEC_CAPABILITIES",
    "EXEC_COMMAND_TYPES",
    "exec_lifecycle_envelope",
    "exec_heartbeat_envelope",
    # world transition
    "build_signalvision_adapter_world_client",
    "build_signalvision_exec_world_client",
    "signalvision_adapter_produces",
    "signalvision_executor_consumes",
    "signalvision_executor_produces",
]
