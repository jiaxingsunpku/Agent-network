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


class SimClock(_Strict):
    """仿真时钟（task5 双时间戳地基）：边缘仿真器(SUMO)的自有时钟，与 envelope.time.event_ts
    (挂钟)并存，作为异步控制回路的对齐/过期判据。完整的世界-边缘时钟同步留作未来一等公民问题。
    """

    sim_time: float = Field(ge=0)  # 仿真时间（秒）
    sim_step: int = Field(ge=0)    # 仿真步序（整数步）


class ObservationPayload(_Strict):
    observation_type: Literal["traffic.intersection"] = "traffic.intersection"
    intersection_id: str = Field(min_length=1)
    approaches: list[Approach] = Field(min_length=1)
    #: task5：仿真时钟（可选；边缘为 SUMO 时带上，供执行体判断状态新鲜度、相位过期）。
    sim_clock: SimClock | None = None


# --------------------------------------------------------------------------- #
# 感知层（视频域）：视频文本事件 payload（topic anp.video.perception.text.v1，P7）
# --------------------------------------------------------------------------- #
class VideoTextEventPayload(_Strict):
    """视频大模型处理后的「文本事件」（docs/video.md）。

    语义：视频智能体作为感知体上报，**原始视频不进 Kafka**。envelope 已承载
    ``message_id`` / ``source.agent_id`` / ``time.event_ts`` / ``quality.confidence``，
    本 payload 只放视频特有字段，不重复 envelope（docs/naming.md §6）。
    """

    observation_type: Literal["video.text"] = "video.text"
    #: 视频源/摄像头标识。
    camera_id: str = Field(min_length=1)
    #: 路段/路口标识（按路段提问的依据）；road_name 与 intersection_id 至少应有其一。
    road_name: str | None = None
    intersection_id: str | None = None
    road_segment: str | None = None
    #: 视频片段时间范围（可选；事件权威时间走 envelope.time.event_ts）。
    start_ts: str | None = None  # ISO8601 UTC，带 Z
    end_ts: str | None = None
    #: 视频大模型产出的文本描述（检索主体，必填）。
    text: str = Field(min_length=1)
    #: 短摘要（可选，便于命中与展示）。
    summary: str | None = None
    #: 事件类别（拥堵/事故/违章…，自由字符串）。命名避免与 envelope.event_type 撞名。
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    #: 结构化抽取（目标类型/计数/车牌等，自由 dict）。
    entities: dict[str, Any] = Field(default_factory=dict)
    #: 视频片段指针（对象存储 URL/路径等），只存指针不存视频。
    artifact_ref: str | None = None
    #: 产出该文本的视频模型名（可选，溯源用）。
    source_model: str | None = None


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
    #: task5：透传感知的仿真时钟（执行体据此得 based_on_sim_step；可选，向后兼容）。
    sim_clock: SimClock | None = None


class GlobalTrafficStatusPayload(_Strict):
    """交通域全局总览（task5 P-10，topic anp.traffic.status.global.v1）。

    系统级 model 聚合所有路口观测产出的**共识指标**（路口数/车辆/等待/均速）；SV 仿真元信息
    （算法/步数/运行状态）不在此，由 SV host 经心跳 ``metadata`` 带、网关合并（保持「共识由系统级算」）。
    """

    status_type: Literal["traffic.global"] = "traffic.global"
    junction_count: int = Field(ge=0)          # 收到观测的路口数
    total_vehicles: int = Field(ge=0)          # 全域瞬时车辆总数
    total_halting: int = Field(ge=0)           # 全域等待（滞留）车辆总数
    mean_speed_kmh: float = Field(ge=0)        # 全域车辆均速
    sim_clock: SimClock | None = None


# --------------------------------------------------------------------------- #
# 控制层：命令 / ack payload（topic anp.traffic.command.v1 / anp.traffic.ack.v1）
# --------------------------------------------------------------------------- #
class CommandPayload(_Strict):
    command_id: str = Field(min_length=1)
    command_type: CommandType
    params: dict[str, Any] = Field(default_factory=dict)


class SignalPhasePayload(_Strict):
    """控制层相位注入（task5，topic anp.traffic.control.phase.v1）。

    执行体（决策外置）算出某路口的目标相位 index，下发给 SV 写灯口注入运行中 SUMO。
    异步语义：写灯口用最近一条未过期相位覆盖内置算法，过期（``based_on_sim_step`` 落后过多）
    回落内置算法。phase_index 合法性（``∈[0, 路口相位数-1]``）由 SV 写灯口本地 Safety Guard 校验。
    """

    control_type: Literal["signal.phase"] = "signal.phase"
    intersection_id: str = Field(min_length=1)
    #: 目标相位 index（SV 原生 phase index，0-based）。
    phase_index: int = Field(ge=0)
    #: 该决策所基于状态的仿真步（过期判据：写灯口比对当前 sim_step 的落后量）。
    based_on_sim_step: int = Field(ge=0)
    #: 对应的仿真时间（秒，可选，辅助溯源/对齐）。
    based_on_sim_time: float | None = Field(default=None, ge=0)
    #: 决策所基于观测的**事件挂钟**（ISO8601 UTC，世界时钟 v1）。写灯口据此算挂钟 age 判过期，
    #: 统一 SUMO/视频等多源（取代只对 SUMO 有意义的 sim_step）；可选、向后兼容。
    based_on_event_ts: str | None = None


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
# 通道：agent 在某 topic 上覆盖哪些实体 key（统一世界自描述用）
# --------------------------------------------------------------------------- #
class Channel(_Strict):
    """agent 的一条「通道」声明：它在某个 ``topic`` 上覆盖哪些实体 ``keys``。

    ``keys`` 为空 = 整条 topic、不分实体；非空 = 只覆盖这些实体（如某路口
    ``["intersection_1_1"]``）。Kafka 订阅仍是 topic 级，key 只进声明 / catalog /
    寻址，客户端按 key 过滤留作后续。
    """

    topic: str = Field(min_length=1)
    keys: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 生命周期 / 心跳 payload
# --------------------------------------------------------------------------- #
class AgentLifecyclePayload(_Strict):
    """注册/下线（统一世界名册 anp.world.agent.lifecycle.v1，docs/protocol.md §3）。"""

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)  # 如 "virtual" / "model"
    capabilities: list[str] = Field(default_factory=list)  # 如 ["perception", "exec"]
    command_types: list[str] = Field(default_factory=list)  # 可接收的命令类型
    #: 统一世界通道声明（加法、向后兼容：旧消息无此字段，按默认值 validate）。
    produces: list[Channel] = Field(default_factory=list)  # 本 agent 产出的通道
    consumes: list[Channel] = Field(default_factory=list)  # 本 agent 订阅的通道
    weight: float = Field(default=1.0, ge=0.0)  # 协作权重，先开槽暂不驱动逻辑
    #: model 专用：自报管辖的成员 agent_id（叶子 agent 留空）。前端据此高亮成员、画治理边。
    members: list[str] = Field(default_factory=list)


class AgentHeartbeatPayload(_Strict):
    """心跳/在线状态（topic anp.traffic.agent.heartbeat.v1）。"""

    status: str = Field(min_length=1)  # online | degraded | offline ...
    last_error: str | None = None
    #: task5 P-10：agent 自报的轻量运行元信息（如 SV host 带 algorithm/sim_step/total_steps/running）。
    #: 加法、向后兼容：旧消息无此字段按默认空 dict validate。
    metadata: dict[str, Any] = Field(default_factory=dict)
