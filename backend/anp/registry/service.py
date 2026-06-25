"""Registry 的 Kafka 接入 —— 后台消费 lifecycle + heartbeat 刷新在线状态。

与网关其它后台消费一致：消费循环把记录喂给纯逻辑 :class:`Registry`，循环本身不
含业务判定。供 :mod:`anp.gateway.consumers` 在守护线程里调用。
"""

from __future__ import annotations

from ..contracts import TrafficTopics, WorldTopics
from ..messaging import make_consumer
from .registry import Registry

#: registry 订阅 —— **双读过渡**（世界级 + 交通级），且 **lifecycle 与 heartbeat 分开读**：
#: - lifecycle（名册，低频）从 **earliest** 重建世界成员（world.lifecycle 已 compact，代价小）；
#: - heartbeat（活性，高频）从 **latest** 读当前心跳——绝不能 earliest，否则历史心跳积压
#:   （traffic.heartbeat 可达数万条）会让 live agent 长时间误判为 offline。
#: 各组迁完后从这两组里撤掉交通级即可。视频域目前无 agent lifecycle 层。
REGISTRY_LIFECYCLE_TOPICS: list[str] = [
    WorldTopics.AGENT_LIFECYCLE,
    TrafficTopics.AGENT_LIFECYCLE,
]
REGISTRY_HEARTBEAT_TOPICS: list[str] = [
    WorldTopics.AGENT_HEARTBEAT,
    TrafficTopics.AGENT_HEARTBEAT,
]
#: 兼容旧调用（合并）。注意 build_registry_consumer 用它+earliest，仅供非 live 工具，
#: live 网关请用上面两组分别 earliest/latest（见 gateway/consumers.py）。
REGISTRY_TOPICS: list[str] = REGISTRY_LIFECYCLE_TOPICS + REGISTRY_HEARTBEAT_TOPICS


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
