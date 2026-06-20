"""vision hub 接入桥子包（P8）。

ANP ↔ vision hub 双向 Kafka 的**翻译边界**：ANP 内部一律说 ANP 契约，本子包是唯一懂 vision hub
原生 topic/envelope 的地方（镜像 ``signalvision/`` adapter 模式）。

- **命令桥** ``VisionHubCommandBridge``：ANP ``anp.video.command.v1`` → vision hub
  ``visionhub.world_model.info.v1``。
- **结果桥** ``VisionHubResultBridge``：vision hub ``edge.observation.result.v1`` →
  ANP ``anp.video.perception.text.v1``（P7 ingest 零改入库）。
- **对账** ``CommandTracker``：用 ``command_id``/``correlation_id`` 记「已发→收到结果」。

设计与映射契约见 docs/adapters.md §5、phases/P8.md。
"""

from __future__ import annotations

from .admin import ensure_visionhub_topics
from .command_bridge import VisionHubCommandBridge
from .config import (
    VISIONHUB_AGENT_ID,
    VISIONHUB_BRIDGE_AGENT_ID,
    VISIONHUB_INFO_TOPIC,
    VISIONHUB_INFO_TYPE,
    VISIONHUB_PERCEPTION_AGENT_ID,
    VISIONHUB_RESULT_TOPIC,
    VISIONHUB_VIDEO_TEXT_EVENT_TYPE,
    VisionHubBridgeConfig,
)
from .mapping import (
    anp_command_to_visionhub_info,
    visionhub_result_to_video_text_envelope,
)
from .result_bridge import VisionHubResultBridge
from .tracker import CommandRecord, CommandTracker

__all__ = [
    # config / 身份 / topic 约定
    "VisionHubBridgeConfig",
    "VISIONHUB_BRIDGE_AGENT_ID",
    "VISIONHUB_PERCEPTION_AGENT_ID",
    "VISIONHUB_AGENT_ID",
    "VISIONHUB_INFO_TOPIC",
    "VISIONHUB_RESULT_TOPIC",
    "VISIONHUB_INFO_TYPE",
    "VISIONHUB_VIDEO_TEXT_EVENT_TYPE",
    # mapping（纯）
    "anp_command_to_visionhub_info",
    "visionhub_result_to_video_text_envelope",
    # 桥
    "VisionHubCommandBridge",
    "VisionHubResultBridge",
    # 对账
    "CommandTracker",
    "CommandRecord",
    # step1 本地便利：确保 vision hub 外部 topic 存在
    "ensure_visionhub_topics",
]
