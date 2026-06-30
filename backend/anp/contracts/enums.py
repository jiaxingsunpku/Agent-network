"""协议枚举 —— event_type、智能体角色、方向、拥堵等级、命令/ack 状态等。

精确语义见 docs/protocol.md §3/§5 与 docs/world-status.md §3/§4。
"""

from __future__ import annotations

from enum import Enum


class SourceSystem(str, Enum):
    """envelope.source.system（docs/protocol.md §1）。"""

    COLLABORATIVE_AGENT = "collaborative_agent"
    PLATFORM = "platform"


class AgentRole(str, Enum):
    """智能体 ID 中的 ``<role>`` 段（docs/naming.md §4）。"""

    PERCEPTION = "perception"
    SYSTEM = "system"
    TASK = "task"
    EXEC = "exec"


class EventType(str, Enum):
    """envelope.event_type 与 topic/payload 的对应见 docs/protocol.md §3。"""

    OBSERVATION_TRAFFIC_INTERSECTION = "observation.traffic.intersection"
    STATUS_TRAFFIC_INTERSECTION = "status.traffic.intersection"
    #: task5：控制层相位注入——执行体把 per-junction 目标相位(phase_index)下发给 SV 写灯口，
    #: 经 anp.traffic.control.phase.v1 异步流转（最近相位覆盖、过期回落内置算法）。
    CONTROL_TRAFFIC_PHASE = "control.traffic.phase"
    #: task5 P-10：交通域全局总览（系统级 model 聚合所有路口的共识指标，供 ANP 前端可视化）。
    STATUS_TRAFFIC_GLOBAL = "status.traffic.global"
    #: P7 视频域：视频大模型处理后的文本事件（docs/video.md）。
    OBSERVATION_VIDEO_TEXT = "observation.video.text"
    COMMAND = "command"
    COMMAND_ACK = "command.ack"
    AGENT_REGISTERED = "agent.registered"
    AGENT_DEREGISTERED = "agent.deregistered"
    AGENT_HEARTBEAT = "agent.heartbeat"


class Direction(str, Enum):
    """路口进口方向（docs/world-status.md §2）。"""

    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"


class CongestionLevel(str, Enum):
    """拥堵等级，沿用老前端 ``HotIntersectionRuntime.state`` 档位（docs/world-status.md §4）。"""

    SMOOTH = "畅通"
    SLOW = "缓行"
    CONGESTED = "拥堵"
    SEVERE = "严重"


class CommandType(str, Enum):
    """下行命令类型白名单（docs/protocol.md §5）。"""

    SET_SIGNAL_PLAN = "set_signal_plan"
    #: 交通信号域：粗粒度「控制信号推理」——启停 / 选择 SignalVision 信号控制算法
    #: （maxpressure/colight/...），映射到 SV `/api/simulation/start`/`/stop`，真驱动 SUMO
    #: （docs/adapters.md §3.5）。区别于细粒度 `set_signal_plan`（写展示层、不驱动 SUMO）。
    CONTROL_SIGNAL_INFERENCE = "control_signal_inference"
    #: 交通信号域：切换 SignalVision 活动路网地图（map_path），映射到 SV `/api/load-map`
    #: （切图前自动停当前仿真以保几何一致，docs/adapters.md §3.6）。仿真级（map 全局）操作。
    SET_SIGNAL_MAP = "set_signal_map"
    #: P8 视频域：请求 vision hub 对某摄像头/路段做一次视频推理、回传文本结果（docs/video.md §10）。
    REQUEST_VIDEO_TEXT = "request_video_text"


class AckStatus(str, Enum):
    """命令回执状态（docs/protocol.md §5「ack status 枚举」）。"""

    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    EXPIRED = "expired"
    FAILED = "failed"
