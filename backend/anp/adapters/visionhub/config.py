"""vision hub 接入桥的配置 —— 集中身份、topic 名与默认参数（P8）。

镜像 SignalVision adapter 的 config 思路：把「外部系统原生 topic / envelope 字段」的知识
**集中在 adapter 内**，ANP 其它组件一律说 ANP 契约。两侧 topic 命名体系不同：

- ANP 侧（本仓库契约）：``anp.video.command.v1`` / ``anp.video.perception.text.v1``。
- vision hub 侧（其现有 Kafka 接口，命名不归本仓库管）：``visionhub.world_model.info.v1``
  （收命令）/ ``edge.observation.result.v1``（出文本结果）。

step1 本地双程序：两套 topic 都落同一本地 broker（``visionhub_bootstrap`` 默认取 ANP bootstrap）。
step2 跨机：vision hub broker 在 wangxuan，``visionhub_bootstrap`` 覆盖为可达地址（见 phases/P8.md）。
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# 身份（docs/naming.md §4：<domain>-<role>-<seq>）
# --------------------------------------------------------------------------- #
#: ANP→vision hub 出口桥的源身份（写在译出的 vision hub info 消息 source.agent_id 上）。
VISIONHUB_BRIDGE_AGENT_ID = "video-visionhub-bridge-001"
#: 结果回流后，在 ANP 侧把文本事件重新发布的**感知体**身份（镜像 traffic-perception-sv-001）。
VISIONHUB_PERCEPTION_AGENT_ID = "video-perception-visionhub-001"
#: 远端 vision hub 视频推理体的逻辑 ID（命令 target.agent_id；step1 由替身脚本扮演）。
VISIONHUB_AGENT_ID = "video-visionhub-001"

# --------------------------------------------------------------------------- #
# vision hub 原生 topic / envelope 约定（adapter 是唯一懂这些的地方）
# --------------------------------------------------------------------------- #
#: vision hub 收「世界模型 info」（含我们注入的 video_inference_request）。
VISIONHUB_INFO_TOPIC = "visionhub.world_model.info.v1"
#: vision hub 出「edge 观测结果」（其 canonical adapter 产 observation.traffic.video_text）。
VISIONHUB_RESULT_TOPIC = "edge.observation.result.v1"
#: 我们注入到 info 消息里的 info_type，约定 vision hub 侧据此触发 dispatch（step2 胶水）。
VISIONHUB_INFO_TYPE = "video_inference_request"
#: vision hub 结果 envelope 的 event_type（其文本观测）；adapter 只翻译这一类。
VISIONHUB_VIDEO_TEXT_EVENT_TYPE = "observation.traffic.video_text"


@dataclass(frozen=True)
class VisionHubBridgeConfig:
    """vision hub 双向桥运行参数。"""

    #: 出口桥源身份（→ vision hub info source.agent_id）。
    bridge_agent_id: str = VISIONHUB_BRIDGE_AGENT_ID
    #: 结果回流后 ANP 侧重新发布文本事件的感知体身份。
    perception_agent_id: str = VISIONHUB_PERCEPTION_AGENT_ID
    #: 远端 vision hub 推理体 ID（命令默认 target；CLI 可覆盖）。
    visionhub_agent_id: str = VISIONHUB_AGENT_ID

    #: vision hub 侧 Kafka bootstrap。step1=None → 取 ANP_BOOTSTRAP/localhost；step2 覆盖为跨机地址。
    visionhub_bootstrap: str | None = None
    #: vision hub 原生 topic 名（真实接入若不同在此覆盖）。
    info_topic: str = VISIONHUB_INFO_TOPIC
    result_topic: str = VISIONHUB_RESULT_TOPIC
    info_type: str = VISIONHUB_INFO_TYPE
    #: 译出 info 消息时声明的发起方标识（payload.requester）。
    requester: str = "anp"
