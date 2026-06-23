"""视频域（P7）：视频文本事件接入 → 集中文本库 → 检索 → 问答。

健康迁移原则（docs/video.md / phases/P7.md）：不搬旧系统运行时，在 ANP 内原生实现
「文本事件接入 → 存储 → 检索 → 问答 → 前端」窄链路；旧项目仅作接口/交互参考。
原始视频不进 Kafka，只接收视频大模型处理后的文本事件。
"""

from __future__ import annotations

from .command_modules import (
    REQUEST_VIDEO_TEXT,
    VIDEO_COMMAND_MODULES,
    CommandModule,
    get_command_module,
    list_command_modules,
)
from .config import VideoConfig, get_video_config
from .orchestrator import (
    DEFAULT_VISIONHUB_ROSTER,
    CommandModuleError,
    NoTargetsError,
    PublishUnavailable,
    VideoTaskOrchestrator,
)
from .qa import VideoQAService
from .retrieval import SearchFilters
from .store import SqliteVideoTextStore, VideoTextStore
from .tasks import (
    SqliteVideoTaskStore,
    TaskCommand,
    TaskScope,
    VideoTask,
    VideoTaskStore,
)

__all__ = [
    "VideoConfig",
    "get_video_config",
    "VideoTextStore",
    "SqliteVideoTextStore",
    "SearchFilters",
    "VideoQAService",
    # 命令模块注册表（P9）
    "REQUEST_VIDEO_TEXT",
    "VIDEO_COMMAND_MODULES",
    "CommandModule",
    "get_command_module",
    "list_command_modules",
    # Task 抽象 + 任务存储（P9）
    "TaskScope",
    "TaskCommand",
    "VideoTask",
    "VideoTaskStore",
    "SqliteVideoTaskStore",
    # 编排器（P9）
    "VideoTaskOrchestrator",
    "DEFAULT_VISIONHUB_ROSTER",
    "CommandModuleError",
    "NoTargetsError",
    "PublishUnavailable",
]
