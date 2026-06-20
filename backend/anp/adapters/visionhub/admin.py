"""vision hub 外部 topic 的本地确保（**仅 step1 便利**，P8）。

``visionhub.world_model.info.v1`` / ``edge.observation.result.v1`` 是 **vision hub 的外部 topic**，
不归 ANP 契约/``deploy/topics`` 管。step1 本地双程序在同一本地 broker 上模拟双向 Kafka，需要这两个
topic 存在；本机 broker 关了 auto-create，故提供这个幂等的 ensure 助手给冒烟/脚本调用。

step2 跨机时 vision hub 用它自己的 broker（已有这些 topic），**不应**调用本助手去远端建 topic
——只在 step1 本地、且显式开启时用（脚本默认本地才 ensure）。
"""

from __future__ import annotations

from .config import VisionHubBridgeConfig


def ensure_visionhub_topics(
    config: VisionHubBridgeConfig | None = None,
    *,
    bootstrap: str | None = None,
    partitions: int = 3,
    replication_factor: int = 1,
) -> list[str]:
    """在 ``bootstrap`` 上幂等创建 vision hub info/result topic。返回本次新建的 topic 名列表。

    已存在则跳过；admin 不可用或建失败时静默忽略（交由后续 producer/consumer 报错）。
    """

    cfg = config or VisionHubBridgeConfig()
    servers = bootstrap or cfg.visionhub_bootstrap
    wanted = [cfg.info_topic, cfg.result_topic]
    try:
        from kafka.admin import KafkaAdminClient, NewTopic
    except Exception:  # noqa: BLE001
        return []

    admin = None
    created: list[str] = []
    try:
        admin = KafkaAdminClient(bootstrap_servers=servers or "localhost:9092")
        existing = set(admin.list_topics())
        to_create = [
            NewTopic(name=t, num_partitions=partitions, replication_factor=replication_factor)
            for t in wanted
            if t not in existing
        ]
        if to_create:
            admin.create_topics(to_create)
            created = [t.name for t in to_create]
    except Exception:  # noqa: BLE001 - 已存在/竞态/admin 异常都不阻塞
        pass
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception:  # noqa: BLE001
                pass
    return created
