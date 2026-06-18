"""Topic 命名与常量 —— 全平台唯一 topic 来源。

规范见 docs/naming.md：物理 topic 格式 ``anp.<domain>.<layer>.<name>.v<major>``。
不要在别处手写 topic 字符串；新增 topic 必须在此登记并同步 docs/naming.md
与 deploy/topics/topics.txt。
"""

from __future__ import annotations

from enum import Enum

#: 平台统一前缀（Agent Network Platform），终结老仓库 agent-network / world-model 双轨命名。
TOPIC_PREFIX = "anp"


class Domain(str, Enum):
    """协同任务域（一个域 = 一个 World Model）。本期只落地 traffic。"""

    TRAFFIC = "traffic"
    # MINE / VIDEO 预留，本期不做（见 docs/naming.md §1）。


class Layer(str, Enum):
    """固定层枚举（见 docs/naming.md §2）。"""

    PERCEPTION = "perception"
    STATUS = "status"
    COMMAND = "command"
    ACK = "ack"
    AGENT_LIFECYCLE = "agent.lifecycle"
    AGENT_HEARTBEAT = "agent.heartbeat"
    DLQ = "dlq"  # 预留，本期不强制（见 docs/protocol.md §4）


def build_topic(domain: Domain | str, layer: Layer | str, name: str | None = None, major: int = 1) -> str:
    """按 ``anp.<domain>.<layer>.[<name>.]v<major>`` 拼装 topic 名。

    command / ack / agent.lifecycle / agent.heartbeat 等层没有独立 ``<name>`` 段，
    传 ``name=None`` 即可。
    """

    domain_s = domain.value if isinstance(domain, Domain) else domain
    layer_s = layer.value if isinstance(layer, Layer) else layer
    parts = [TOPIC_PREFIX, domain_s, layer_s]
    if name:
        parts.append(name)
    return ".".join(parts) + f".v{major}"


class TrafficTopics:
    """交通域 v1 topic 清单（docs/naming.md §2「交通域 v1 Topic 清单」）。"""

    OBSERVATION = build_topic(Domain.TRAFFIC, Layer.PERCEPTION, "observation")
    STATUS_INTERSECTION = build_topic(Domain.TRAFFIC, Layer.STATUS, "intersection")
    COMMAND = build_topic(Domain.TRAFFIC, Layer.COMMAND)
    ACK = build_topic(Domain.TRAFFIC, Layer.ACK)
    AGENT_LIFECYCLE = build_topic(Domain.TRAFFIC, Layer.AGENT_LIFECYCLE)
    AGENT_HEARTBEAT = build_topic(Domain.TRAFFIC, Layer.AGENT_HEARTBEAT)
    DLQ = build_topic(Domain.TRAFFIC, Layer.DLQ)  # 预留


#: 本期需要建立的交通域 topic（DLQ 预留也建好，消费失败可落 DLQ）。
ALL_TRAFFIC_TOPICS: tuple[str, ...] = (
    TrafficTopics.OBSERVATION,
    TrafficTopics.STATUS_INTERSECTION,
    TrafficTopics.COMMAND,
    TrafficTopics.ACK,
    TrafficTopics.AGENT_LIFECYCLE,
    TrafficTopics.AGENT_HEARTBEAT,
    TrafficTopics.DLQ,
)
