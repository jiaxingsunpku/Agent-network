"""各 event_type 的 payload 模型 —— envelope 外壳之内、按 topic 区分的负载。

字段定义严格对齐 docs/protocol.md §3/§5 与 docs/world-status.md §2/§3。
这些模型既用于运行时结构化校验，也用于由 scripts/gen_schemas.py 导出 schemas/*.json。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import AckStatus, CommandType, CongestionLevel, Direction


class _Strict(BaseModel):
    """所有 payload 的基类：禁止多余字段，及早暴露契约偏差。"""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# 感知层：观测 payload（topic anp.traffic.perception.observation.v1）
# --------------------------------------------------------------------------- #
class Approach(_Strict):
    """单进口方向的一次采样观测（docs/world-status.md §2）。"""

    direction: Direction
    #: 本采样间隔内**通过**的车辆数（吞吐量，用于换算流量）——不是瞬时车辆数。
    vehicle_count: int = Field(ge=0)
    #: 瞬时**滞留（停车）**车辆数（用于估排队）。
    halting_count: int = Field(ge=0)
    mean_speed_mps: float = Field(ge=0)
    #: 可选；缺省时由系统级智能体按速度推导（docs/world-status.md §4）。
    mean_delay_sec: float | None = Field(default=None, ge=0)


class ObservationPayload(_Strict):
    observation_type: Literal["traffic.intersection"] = "traffic.intersection"
    intersection_id: str = Field(min_length=1)
    approaches: list[Approach] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 状态层：路口 World Status payload（topic anp.traffic.status.intersection.v1）
# --------------------------------------------------------------------------- #
class StatusWindow(_Strict):
    """滚动窗口元信息（docs/world-status.md §1/§3）。"""

    start: str  # ISO8601 UTC，带 Z
    end: str
    size_sec: int = Field(gt=0)
    sample_count: int = Field(ge=0)


class ApproachStatus(_Strict):
    direction: Direction
    queue_length_m: float = Field(ge=0)
    flow_veh_h: float = Field(ge=0)
    mean_speed_kmh: float = Field(ge=0)


class IntersectionStatusPayload(_Strict):
    status_type: Literal["traffic.intersection"] = "traffic.intersection"
    intersection_id: str = Field(min_length=1)
    window: StatusWindow
    queue_length_m: float = Field(ge=0)
    flow_veh_h: float = Field(ge=0)
    mean_speed_kmh: float = Field(ge=0)
    mean_delay_sec: float = Field(ge=0)
    congestion_level: CongestionLevel
    congestion_index: float = Field(ge=0, le=1)
    approaches: list[ApproachStatus] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 控制层：命令 / ack payload（topic anp.traffic.command.v1 / anp.traffic.ack.v1）
# --------------------------------------------------------------------------- #
class CommandPayload(_Strict):
    command_id: str = Field(min_length=1)
    command_type: CommandType
    params: dict[str, Any] = Field(default_factory=dict)


class SafetyDecision(_Strict):
    """执行端本地 Safety Guard 的判定（权威安全闭环在执行端，docs/protocol.md §5/§7）。"""

    allowed: bool
    decision: str | None = None
    reason: str | None = None


class AckPayload(_Strict):
    command_id: str = Field(min_length=1)  # 回指原命令
    command_type: CommandType
    status: AckStatus
    safety: SafetyDecision | None = None


# --------------------------------------------------------------------------- #
# 生命周期 / 心跳 payload
# --------------------------------------------------------------------------- #
class AgentLifecyclePayload(_Strict):
    """注册/下线（topic anp.traffic.agent.lifecycle.v1，docs/protocol.md §3）。"""

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)  # 如 "virtual"
    capabilities: list[str] = Field(default_factory=list)  # 如 ["perception", "exec"]
    command_types: list[str] = Field(default_factory=list)  # 可接收的命令类型


class AgentHeartbeatPayload(_Strict):
    """心跳/在线状态（topic anp.traffic.agent.heartbeat.v1）。"""

    status: str = Field(min_length=1)  # online | degraded | offline ...
    last_error: str | None = None
