"""外部源接入适配器（adapters）—— 把外部系统桥接到统一 ANP 契约。

每个外部源一个子包，职责单一、互不耦合，**adapter 是唯一懂该源原生结构的地方**，
ANP 其它组件一律说 ANP 契约。适配器只做接入与结构映射，**不在此散搓 envelope、
不计算 World Status**（共识指标一律由系统级智能体算，AGENTS.md §3.2）。

已落地子包：

- ``signalvision/``：智能交通推理系统——感知侧（junction→按方向观测）+ 执行侧（信号控制，P5/P6）。
- ``visionhub/``：视频推理系统——命令桥（ANP 视频命令→vision hub info）+ 结果桥（vision hub
  文本结果→ANP 视频感知层），ANP↔vision hub 双向 Kafka 翻译边界（P8）。

设计与映射契约见 docs/adapters.md。
"""

from __future__ import annotations

from .signalvision import (
    SignalVisionAdapter,
    SignalVisionAdapterConfig,
    SignalVisionClient,
    map_junction_to_observation,
)
from .visionhub import (
    VisionHubBridgeConfig,
    VisionHubCommandBridge,
    VisionHubResultBridge,
    anp_command_to_visionhub_info,
    visionhub_result_to_video_text_envelope,
)

__all__ = [
    # signalvision
    "SignalVisionAdapter",
    "SignalVisionAdapterConfig",
    "SignalVisionClient",
    "map_junction_to_observation",
    # visionhub（P8）
    "VisionHubBridgeConfig",
    "VisionHubCommandBridge",
    "VisionHubResultBridge",
    "anp_command_to_visionhub_info",
    "visionhub_result_to_video_text_envelope",
]
