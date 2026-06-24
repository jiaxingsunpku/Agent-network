"""Registry 的 Kafka 接入 —— 后台消费 lifecycle + heartbeat 刷新在线状态。

与网关其它后台消费一致：消费循环把记录喂给纯逻辑 :class:`Registry`，循环本身不
含业务判定。供 :mod:`anp.gateway.consumers` 在守护线程里调用。
"""

from __future__ import annotations

from ..contracts import TrafficTopics, WorldTopics
from ..messaging import make_consumer
from .registry import Registry

#: registry 订阅的 lifecycle/heartbeat topic —— **双读过渡**：世界级 + 交通级。
#: 世界级是目标名册；交通级保留，让尚未迁到 WorldClient 的交通体（如 SV 感知/执行体）
#: 仍可见。各组迁完后从这里撤掉交通级即可。视频域目前无 agent lifecycle 层。
REGISTRY_TOPICS: list[str] = [
    WorldTopics.AGENT_LIFECYCLE,
    WorldTopics.AGENT_HEARTBEAT,
    TrafficTopics.AGENT_LIFECYCLE,
    TrafficTopics.AGENT_HEARTBEAT,
]


class RegistryConsumer:
    """消费 lifecycle/heartbeat 两个 topic，持续刷新 :class:`Registry`。"""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.applied = 0
        self.skipped = 0

    def run(self, consumer) -> None:
        """消费 ``consumer`` 直到其迭代结束（live: 永不结束）。非法/无关消息跳过计数。"""

        from ..contracts import Envelope
        from pydantic import ValidationError

        for record in consumer:
            try:
                env = Envelope.model_validate(record.value)
            except ValidationError:
                self.skipped += 1
                continue
            if self.registry.apply_envelope(env):
                self.applied += 1
            else:
                self.skipped += 1


def build_registry_consumer(
    registry: Registry, *, bootstrap: str | None = None, group_id: str = "anp-gateway-registry"
):
    """构造订阅 lifecycle+heartbeat 的 consumer（live 模式）。返回 ``(svc, consumer)``。"""

    consumer = make_consumer(
        REGISTRY_TOPICS,
        group_id=group_id,
        bootstrap_servers=bootstrap,
        auto_offset_reset="earliest",  # 从头重建世界名册（compacted lifecycle 代价小）
    )
    return RegistryConsumer(registry), consumer
