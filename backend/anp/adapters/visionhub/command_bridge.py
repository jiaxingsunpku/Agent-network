"""命令桥：ANP ``anp.video.command.v1`` → vision hub ``visionhub.world_model.info.v1``（P8）。

ANP 侧「请求视频推理」命令的**唯一出口翻译点**。消费 ANP 命令 envelope，经 :mod:`mapping`
译成 vision hub world-model info 消息（其原生 envelope），发到 vision hub 的 info topic；并在
:class:`CommandTracker` 记一笔「已发」。``translate``/``handle`` 纯逻辑可单测，``run`` 跑 Kafka 循环。

它是**域级出口桥**（不是按 target 过滤的执行体）：转发所有 ``request_video_text`` 命令，目标
``target_agent_id`` 随 info payload 带给 vision hub。**不算 World Status、不做 Safety Guard**
（视频推理请求非控制动作；vision hub 侧本地有自己的限流/Safety，step2）。
"""

from __future__ import annotations

from typing import Any

from anp.contracts import Envelope
from anp.messaging import make_consumer, make_producer

from .config import VisionHubBridgeConfig
from .mapping import anp_command_to_visionhub_info
from .tracker import CommandTracker


class VisionHubCommandBridge:
    """ANP 视频命令 → vision hub info 的出口桥。"""

    def __init__(self, config: VisionHubBridgeConfig | None = None, *, tracker: CommandTracker | None = None) -> None:
        self.config = config or VisionHubBridgeConfig()
        self.tracker = tracker or CommandTracker()
        self.forwarded = 0
        self.skipped = 0

    # -- 纯翻译（无 IO，可单测）------------------------------------------- #
    def translate(self, env: Envelope) -> dict[str, Any] | None:
        return anp_command_to_visionhub_info(
            env,
            source_agent_id=self.config.bridge_agent_id,
            requester=self.config.requester,
            info_type=self.config.info_type,
        )

    # -- 一条命令：译 → 记账 → 发 vision hub info ------------------------- #
    def handle(self, env: Envelope, producer) -> dict[str, Any] | None:
        info = self.translate(env)
        if info is None:
            self.skipped += 1
            return None
        ip = info["payload"]
        cid = ip["command_id"]
        self.tracker.mark_dispatched(cid, camera_id=ip.get("camera_id"), road_name=ip.get("road_name"))
        # vision hub 是外部原生 topic：发原始 dict（key=correlation 保同命令有序），不走 ANP envelope publish。
        producer.send(self.config.info_topic, key=cid, value=info)
        self.forwarded += 1
        return info

    # -- Kafka 循环（消费 ANP 命令；producer 指向 vision hub broker）------ #
    def run(self, consumer, producer) -> None:
        try:
            for record in consumer:
                try:
                    env = Envelope.model_validate(record.value)
                except Exception:  # noqa: BLE001 - 非法消息丢弃
                    self.skipped += 1
                    continue
                self.handle(env, producer)
        finally:
            producer.flush()

    # -- 便捷工厂（脚本用）------------------------------------------------ #
    def make_anp_consumer(self, *, bootstrap: str | None = None, group_id: str = "anp-visionhub-command-bridge", **kw):
        from anp.contracts import VideoTopics

        return make_consumer(VideoTopics.COMMAND, group_id=group_id, bootstrap_servers=bootstrap, **kw)

    def make_visionhub_producer(self, **kw):
        return make_producer(bootstrap_servers=self.config.visionhub_bootstrap, **kw)
