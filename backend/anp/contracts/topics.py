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
    """协同任务域（一个域 = 一个 World Model）。"""

    TRAFFIC = "traffic"
    #: P7：视频监控 World Model（事件文本问答），见 docs/video.md。
    VIDEO = "video"
    #: 平台级「世界」命名空间：跨域名册/发现的 meta 平面（lifecycle/heartbeat），
    #: 不是某个城市问题域——所有域的 agent 都往这里自注册 + 心跳。
    WORLD = "world"
    # MINE 预留，本期不做（见 docs/naming.md §1）。


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


class VideoTopics:
    """视频域 v1 topic 清单（docs/naming.md §2「视频域 v1 Topic 清单」）。

    P7：视频智能体作为感知体，把视频大模型处理后的「文本事件」发到感知层 topic
    （``PERCEPTION_TEXT``）；原始视频不进 Kafka。
    P8：补控制层 ``COMMAND``——ANP 下发「请求视频推理」命令，经 adapters/visionhub
    译给 vision hub，结果文本回流仍走 ``PERCEPTION_TEXT``（闭合对称双向环，docs/video.md §10）。
    """

    PERCEPTION_TEXT = build_topic(Domain.VIDEO, Layer.PERCEPTION, "text")
    #: P8：下行「请求视频推理」命令（anp.video.command.v1）。
    COMMAND = build_topic(Domain.VIDEO, Layer.COMMAND)
    DLQ = build_topic(Domain.VIDEO, Layer.DLQ)  # 预留


#: 视频域需要建立的 topic（P7 感知文本 + P8 命令 + DLQ 预留）。
ALL_VIDEO_TOPICS: tuple[str, ...] = (
    VideoTopics.PERCEPTION_TEXT,
    VideoTopics.COMMAND,
    VideoTopics.DLQ,
)


class WorldTopics:
    """世界级 meta 平面 topic（跨域名册/发现，docs/naming.md）。

    lifecycle 是统一世界名册（compacted，key=agent_id 留每个 agent 最新一条）；
    heartbeat 是活性流（短保留）。所有域的 agent 都往这里自注册 + 心跳，
    registry 读这两条重建世界视图。
    """

    AGENT_LIFECYCLE = build_topic(Domain.WORLD, Layer.AGENT_LIFECYCLE)  # anp.world.agent.lifecycle.v1
    AGENT_HEARTBEAT = build_topic(Domain.WORLD, Layer.AGENT_HEARTBEAT)  # anp.world.agent.heartbeat.v1


#: 世界级 meta topic（lifecycle compacted、heartbeat 短保留，见 deploy/topics）。
ALL_WORLD_TOPICS: tuple[str, ...] = (
    WorldTopics.AGENT_LIFECYCLE,
    WorldTopics.AGENT_HEARTBEAT,
)
