"""视频文本事件入库（P7）。

两条入口：
1. HTTP：``POST /api/agent-network/video-text/events``（见 routes.py）直接 append；
2. Kafka：:class:`VideoTextIngestConsumer` 订阅 ``anp.video.perception.text.v1`` 写库。

Kafka 是规范上行路径（视频感知体作为感知智能体发布），HTTP 是便捷/桥接入口。
两者最终都落到同一 :class:`VideoTextStore`，按 ``message_id`` 幂等去重。
"""

from __future__ import annotations

import threading
from typing import Any

from anp.contracts import Envelope, EventType, VideoTopics
from anp.messaging import make_consumer

from .store import VideoTextStore


def ingest_value(store: VideoTextStore, value: Any) -> str:
    """把一条原始消息（dict）解析为 envelope 并入库。纯逻辑、可单测（不依赖 Kafka）。

    返回 ``"new"`` / ``"duplicate"`` / ``"skipped"``。
    """

    try:
        env = Envelope.model_validate(value)
    except Exception:  # noqa: BLE001 - 不合 schema 丢弃
        return "skipped"
    if env.event_type != EventType.OBSERVATION_VIDEO_TEXT:
        return "skipped"
    try:
        is_new = store.append(env)
    except Exception:  # noqa: BLE001 - 单条入库失败不影响整体
        return "skipped"
    return "new" if is_new else "duplicate"


class VideoTextIngestConsumer:
    """订阅视频感知层 topic，把文本事件写入文本库。"""

    def __init__(
        self,
        store: VideoTextStore,
        *,
        group_id: str = "anp-video-ingest",
        bootstrap_servers: str | None = None,
        auto_offset_reset: str = "latest",
        consumer_timeout_ms: int | float = float("inf"),
    ) -> None:
        self.store = store
        self._consumer = make_consumer(
            VideoTopics.PERCEPTION_TEXT,
            group_id=group_id,
            bootstrap_servers=bootstrap_servers,
            auto_offset_reset=auto_offset_reset,
            consumer_timeout_ms=consumer_timeout_ms,
        )
        self._stop = threading.Event()
        self.ingested = 0
        self.duplicate = 0
        self.skipped = 0

    def handle(self, value: Any) -> bool:
        """处理一条消息：校验 → 仅收视频文本事件 → 幂等入库。返回是否新写入。"""

        outcome = ingest_value(self.store, value)
        if outcome == "new":
            self.ingested += 1
        elif outcome == "duplicate":
            self.duplicate += 1
        else:
            self.skipped += 1
        return outcome == "new"

    def drain(self) -> int:
        """消费当前可得消息直到 poll 超时（依赖有限 consumer_timeout_ms；冒烟/批量用）。

        返回本次新写入条数。
        """

        before = self.ingested
        for msg in self._consumer:
            self.handle(msg.value)
        try:
            self._consumer.commit()
        except Exception:  # noqa: BLE001
            pass
        return self.ingested - before

    def run_forever(self) -> None:
        """阻塞消费循环（live；靠 close() 收尾）。"""

        try:
            for msg in self._consumer:
                if self._stop.is_set():
                    break
                self.handle(msg.value)
                try:
                    self._consumer.commit()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._consumer.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._consumer.close()
        except Exception:  # noqa: BLE001
            pass
