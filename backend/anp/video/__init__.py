"""视频域（P7）：视频文本事件接入 → 集中文本库 → 检索 → 问答。

健康迁移原则（docs/video.md / phases/P7.md）：不搬旧系统运行时，在 ANP 内原生实现
「文本事件接入 → 存储 → 检索 → 问答 → 前端」窄链路；旧项目仅作接口/交互参考。
原始视频不进 Kafka，只接收视频大模型处理后的文本事件。
"""

from __future__ import annotations

from .config import VideoConfig, get_video_config
from .qa import VideoQAService
from .retrieval import SearchFilters
from .store import SqliteVideoTextStore, VideoTextStore

__all__ = [
    "VideoConfig",
    "get_video_config",
    "VideoTextStore",
    "SqliteVideoTextStore",
    "SearchFilters",
    "VideoQAService",
]
