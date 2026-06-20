"""结果桥：vision hub ``edge.observation.result.v1`` → ANP ``anp.video.perception.text.v1``（P8）。

把 vision hub 产出的 ``observation.traffic.video_text`` 结果译成 ANP 视频文本事件 envelope，
经统一 envelope builder 发布到 ANP 视频感知层——**P7 ingest 零改入库**（闭合双向环）。回流时按
``trace.parent_trace_id``（= 原 ``command_id``）在 :class:`CommandTracker` 记一笔「已回流」。

镜像 SignalVision 感知 adapter 的发布侧：在 ANP 侧用**本桥的感知体身份**重新发布（不冒用 vision
hub 内部 agent_id）。``translate``/``handle`` 纯逻辑可单测，``run`` 跑 Kafka 循环。**不算 World Status。**
"""

from __future__ import annotations

from typing import Any

from anp.contracts import Envelope, VideoTopics
from anp.messaging import make_consumer, make_producer, publish

from .config import VisionHubBridgeConfig
from .mapping import visionhub_result_to_video_text_envelope
from .tracker import CommandTracker


class VisionHubResultBridge:
    """vision hub 文本结果 → ANP 视频感知层文本事件的入口桥。"""

    def __init__(self, config: VisionHubBridgeConfig | None = None, *, tracker: CommandTracker | None = None) -> None:
        self.config = config or VisionHubBridgeConfig()
        self.tracker = tracker or CommandTracker()
        self.republished = 0
        self.skipped = 0

    # -- 纯翻译（无 IO，可单测）------------------------------------------- #
    def translate(self, value: Any) -> Envelope | None:
        return visionhub_result_to_video_text_envelope(
            value, perception_agent_id=self.config.perception_agent_id
        )

    # -- 一条结果：译 → 记账 → 发 ANP 感知层文本事件 --------------------- #
    def handle(self, value: Any, producer) -> Envelope | None:
        env = self.translate(value)
        if env is None:
            self.skipped += 1
            return None
        self.tracker.mark_returned(env.trace.parent_trace_id, env.message_id)
        publish(producer, VideoTopics.PERCEPTION_TEXT, env)
        self.republished += 1
        return env

    # -- Kafka 循环（消费 vision hub 结果；producer 指向 ANP broker）----- #
    def run(self, consumer, producer) -> None:
        try:
            for record in consumer:
                self.handle(record.value, producer)
        finally:
            producer.flush()

    # -- 便捷工厂（脚本用）------------------------------------------------ #
    def make_visionhub_consumer(self, *, group_id: str = "anp-visionhub-result-bridge", **kw):
        return make_consumer(
            self.config.result_topic,
            group_id=group_id,
            bootstrap_servers=self.config.visionhub_bootstrap,
            **kw,
        )

    def make_anp_producer(self, *, bootstrap: str | None = None, **kw):
        return make_producer(bootstrap_servers=bootstrap, **kw)
