"""Registry 常量 —— 心跳新鲜度阈值、默认 v1 智能体注册种子。

在线/降级/离线由「距上次心跳的时长」推导（docs/gateway-api.md §1.1 智能体节点
status 由 heartbeat 在线/降级映射）。调阈值改这里即可，全平台统一。
"""

from __future__ import annotations

from ..contracts import Channel, CommandType, TrafficTopics

#: 心跳在此时长内（秒）视为「新鲜」，采用上报状态（online/degraded）。
#: 虚拟体默认每 5s 一次心跳，留 3 次容忍。
HEARTBEAT_ONLINE_TTL_SEC = 15.0
#: 超过在线 TTL 但仍在此时长内 → 降级（degraded）。
HEARTBEAT_OFFLINE_TTL_SEC = 30.0

#: 默认 v1 智能体注册种子：即使没有 live 心跳，网关也能把它们渲染成节点
#: （状态显示 syncing，直到收到心跳）。lifecycle/heartbeat 到达后再覆盖。
#: 种子也带通道（keys 留空=整条 topic），让 catalog 冷启动就完整；live 自注册会刷新。
DEFAULT_AGENTS: tuple[dict, ...] = (
    {
        "agent_id": "traffic-virtual-001",
        "agent_type": "virtual",
        "capabilities": ["perception", "exec"],
        "command_types": [CommandType.SET_SIGNAL_PLAN.value],
        "produces": [Channel(topic=TrafficTopics.OBSERVATION), Channel(topic=TrafficTopics.ACK)],
        "consumes": [Channel(topic=TrafficTopics.COMMAND)],
    },
    {
        "agent_id": "traffic-system-001",
        "agent_type": "system",
        "capabilities": ["aggregation"],
        "command_types": [],  # 系统级智能体不接收下行命令
        "produces": [Channel(topic=TrafficTopics.STATUS_INTERSECTION)],
        "consumes": [Channel(topic=TrafficTopics.OBSERVATION)],
    },
)
