"""Kafka 生产/消费薄封装 —— 全平台统一的 envelope 收发口。

只做三件事：
1. 用 ``Serializer`` / ``Deserializer`` 子类承载 JSON + UTF-8 编解码，消除 P1 里
   lambda 序列化器在 kafka-python 3.x 下的 DeprecationWarning。
2. 提供 :func:`make_producer` / :func:`make_consumer`，集中默认参数（acks、重试、
   手动提交等），避免各 agent 各搓一套。
3. :func:`publish` 按 ``anp.contracts.partition_key`` 计算分区键后发送 envelope，
   保证同实体有序（docs/protocol.md §4）。

不在此做任何业务逻辑或世界状态计算。
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from kafka import KafkaConsumer, KafkaProducer
from kafka.serializer import Deserializer, Serializer

from .contracts import Envelope, partition_key

#: 默认 bootstrap，可用环境变量覆盖（与冒烟脚本一致）。
DEFAULT_BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")


# --------------------------------------------------------------------------- #
# 序列化器（取代 lambda，消除 DeprecationWarning）
# --------------------------------------------------------------------------- #
class JsonSerializer(Serializer):
    """dict → UTF-8 JSON bytes（``ensure_ascii=False`` 保留中文等级名）。"""

    def serialize(self, topic: str, value: Any) -> bytes | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False).encode("utf-8")


class JsonDeserializer(Deserializer):
    """UTF-8 JSON bytes → dict。"""

    def deserialize(self, topic: str, value: bytes | None) -> Any:
        if value is None:
            return None
        return json.loads(value.decode("utf-8"))


class StringSerializer(Serializer):
    """str → UTF-8 bytes（分区键）。"""

    def serialize(self, topic: str, value: Any) -> bytes | None:
        if value is None:
            return None
        return str(value).encode("utf-8")


class StringDeserializer(Deserializer):
    """UTF-8 bytes → str（分区键）。"""

    def deserialize(self, topic: str, value: bytes | None) -> Any:
        if value is None:
            return None
        return value.decode("utf-8")


# --------------------------------------------------------------------------- #
# 工厂
# --------------------------------------------------------------------------- #
def make_producer(bootstrap_servers: str | None = None, **overrides: Any) -> KafkaProducer:
    """构造统一配置的 KafkaProducer（acks=all、有限重试，保证落盘可靠）。"""

    kwargs: dict[str, Any] = dict(
        bootstrap_servers=bootstrap_servers or DEFAULT_BOOTSTRAP,
        key_serializer=StringSerializer(),
        value_serializer=JsonSerializer(),
        acks="all",
        retries=3,
        linger_ms=20,
    )
    kwargs.update(overrides)
    return KafkaProducer(**kwargs)


def make_consumer(
    topics: str | Iterable[str],
    *,
    group_id: str | None = None,
    bootstrap_servers: str | None = None,
    auto_offset_reset: str = "latest",
    enable_auto_commit: bool = False,
    consumer_timeout_ms: int = float("inf"),  # type: ignore[assignment]
    **overrides: Any,
) -> KafkaConsumer:
    """构造统一配置的 KafkaConsumer 并订阅 ``topics``。

    默认手动提交（``enable_auto_commit=False``）+ ``latest``，由调用方决定语义；
    冒烟/批量场景可传 ``consumer_timeout_ms`` 让 poll 在空闲后自然退出。
    """

    topic_list = [topics] if isinstance(topics, str) else list(topics)
    kwargs: dict[str, Any] = dict(
        bootstrap_servers=bootstrap_servers or DEFAULT_BOOTSTRAP,
        group_id=group_id,
        key_deserializer=StringDeserializer(),
        value_deserializer=JsonDeserializer(),
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=enable_auto_commit,
        consumer_timeout_ms=consumer_timeout_ms,
    )
    kwargs.update(overrides)
    consumer = KafkaConsumer(*topic_list, **kwargs)
    return consumer


# --------------------------------------------------------------------------- #
# 发送
# --------------------------------------------------------------------------- #
def publish(producer: KafkaProducer, topic: str, env: Envelope, *, flush: bool = False):
    """按契约分区键发送一个 envelope；返回 kafka-python 的 future。"""

    key = partition_key(env)
    future = producer.send(topic, key=key, value=env.to_wire())
    if flush:
        producer.flush()
    return future
