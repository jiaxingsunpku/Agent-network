"""SignalVision 感知 adapter 配置 —— 集中可调参数与默认值。

默认把一个代表性 SV junction 映射到平台 `gg-xiongchu-minzu` 路口，使本 adapter
作为新的感知源时，端到端能与 v1 虚拟感知体一样点亮网关同一路口。真实接入时按
SV 实际 junction_id 覆盖 `junction_map`。详见 docs/adapters.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 本 adapter 登记的智能体 ID（docs/naming.md §4：<domain>-<role>-<seq>，role=perception）。
SV_ADAPTER_AGENT_ID = "traffic-perception-sv-001"
#: agent_type：沿用老仓库 bridge 的数据源标识，便于 registry/UI 区分来源。
SV_ADAPTER_AGENT_TYPE = "signalvision"
#: P6 信号控制执行体 ID（role=exec，与感知体同属 SV 接入家族；docs/naming.md §4）。
SV_EXEC_AGENT_ID = "traffic-exec-sv-001"
#: 默认 SV junction → 平台 intersection_id 映射（与网关拓扑 `gg-xiongchu-minzu` 对齐）。
DEFAULT_JUNCTION_MAP: dict[str, str] = {"intersection_1_1": "gg-xiongchu-minzu"}

#: lane → 罗盘方向归并策略（详见 docs/adapters.md）。
#:   - "auto"：先从 lane_id 抽罗盘 token（north/北/n…），抽不到再按序号轮询 N/S/E/W。
#:   - "round_robin"：一律按排序后序号轮询，不看 lane_id。
DIRECTION_STRATEGY_AUTO = "auto"
DIRECTION_STRATEGY_ROUND_ROBIN = "round_robin"


@dataclass(frozen=True)
class SignalVisionAdapterConfig:
    """SignalVision 感知 adapter 的运行参数。"""

    agent_id: str = SV_ADAPTER_AGENT_ID
    agent_type: str = SV_ADAPTER_AGENT_TYPE
    #: SV Dashboard HTTP API 根地址（真实接入时覆盖）。
    sv_base_url: str = "http://127.0.0.1:8080"
    http_timeout_sec: float = 3.0
    #: 轮询间隔（秒）。每轮取 junction detail 并发布观测 + 心跳。
    poll_interval_sec: float = 2.0
    #: SV junction_id → 平台 intersection_id。只接入此表内的 junction。
    junction_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_JUNCTION_MAP))
    #: lane→方向策略。
    direction_strategy: str = DIRECTION_STRATEGY_AUTO
    #: 观测置信度（写入 envelope.quality.confidence；系统级 MIN_CONFIDENCE=0.3 过滤）。
    confidence: float = 0.95
    #: 站点/区域（仅信息性，网关按 object_id=intersection_id 取数）。
    site_id: str | None = "signalvision-dashboard"
    region_id: str | None = None


@dataclass(frozen=True)
class SignalVisionExecConfig:
    """SignalVision 信号控制**执行侧** adapter 的运行参数（P6）。

    执行体订阅控制层命令、调 SV 写端点。命令 `scope.object_id`（intersection_id）经
    `junction_map`（与感知侧同向：SV junction → 平台 intersection）反查目标 SV junction。
    """

    agent_id: str = SV_EXEC_AGENT_ID
    agent_type: str = SV_ADAPTER_AGENT_TYPE
    #: SV Dashboard HTTP API 根地址（真实接入时覆盖）。
    sv_base_url: str = "http://127.0.0.1:8080"
    http_timeout_sec: float = 3.0
    #: SV junction_id → 平台 intersection_id（与感知侧同向，执行时反查）。
    junction_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_JUNCTION_MAP))
    #: 符号相位名（desired_phase）→ SV phase_state 串映射。空 = 透传符号名。
    #: 真实接入若 SV 用 SUMO 相位串编码，在此配置覆盖（docs/adapters.md §4）。
    phase_state_map: dict[str, str] = field(default_factory=dict)
    #: 心跳间隔（秒）。执行体定期上报，携 SV 可达性。
    heartbeat_interval_sec: float = 5.0
