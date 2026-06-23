"""视频域「命令模块族」注册表（P9）。

把视频前端那几类能力**翻转**为 ANP 下发的命令：每类能力 = 一种下行命令模块，由编排器
扇出给外部 vision hub 执行（ANP 不在本地实现检测/拉流/CV）。本注册表是**轻量声明**——
声明可下发的命令模块、其对应契约命令类型、目标体筛选规则与参数模板，供编排器与前端共用。

MVP 只**落地** ``request_video_text``（请求一次视频文本推理，P8 链路已通）；
``video.detect`` / ``video.stream.attach`` / ``video.model.select`` 仅**占位声明**
（``implemented=False``、``command_type=None``）——它们是 vision hub 的职责，本期不实现执行端，
只在前端诚实标注「外部系统(vision hub)」并预留命令外形（R4：保持薄编排，不强行实现无后端的能力）。

设计与边界见 tasks/task1/readme.md §3、docs/adapters.md §5、docs/video.md §10。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anp.contracts import CommandType

#: MVP 主命令模块 key（唯一落地执行端的模块）。
REQUEST_VIDEO_TEXT = "request_video_text"


@dataclass(frozen=True)
class CommandModule:
    """一个可下发的视频命令模块声明。"""

    #: 模块 key（前端选择 / 任务 module 字段）。
    key: str
    #: 中文展示名。
    title: str
    #: 一句话说明（前端展示）。
    description: str
    #: 对应契约命令类型；占位模块为 None（无执行端、不可下发）。
    command_type: CommandType | None
    #: MVP 是否已落地执行端（占位模块为 False）。
    implemented: bool
    #: 目标体筛选标签：编排器据此从 vision hub roster/registry 选目标体（capability/tag）。
    target_capability: str = "video_inference"
    #: 参数模板（仅文档/前端提示用；实际参数由编排器按 task scope 填充）。
    param_template: dict[str, Any] = field(default_factory=dict)


#: 命令模块注册表（单一来源；前端与编排器共用 key 与展示名）。
VIDEO_COMMAND_MODULES: dict[str, CommandModule] = {
    REQUEST_VIDEO_TEXT: CommandModule(
        key=REQUEST_VIDEO_TEXT,
        title="视频文本推理",
        description="请求 vision hub 对某摄像头/路段做一次视频推理、回传文本语义事件（事件摘要的真实能力）。",
        command_type=CommandType.REQUEST_VIDEO_TEXT,
        implemented=True,
        target_capability="video_inference",
        param_template={
            "camera_id": "cam-…",
            "road_name": "民族大道",
            "intersection_id": None,
            "time_window": {"time_from": None, "time_to": None},
            "prompt": "该路段最近有没有事故、拥堵或违章？",
        },
    ),
    # —— 以下为占位声明：vision hub 的职责，ANP 不实现执行端（仅外形 + 文档）——
    "video.detect": CommandModule(
        key="video.detect",
        title="目标检测",
        description="边缘目标检测/结构化识别——由 vision hub/底层 VisionHub 执行，ANP 只收回流文本，不在本地跑 CV。",
        command_type=None,
        implemented=False,
        target_capability="video_detect",
    ),
    "video.stream.attach": CommandModule(
        key="video.stream.attach",
        title="视频流接入",
        description="码流接入/拉流——vision hub 职责；ANP 不解码、不播放原始视频，只接收文本语义事件。",
        command_type=None,
        implemented=False,
        target_capability="video_stream",
    ),
    "video.model.select": CommandModule(
        key="video.model.select",
        title="模型管理",
        description="视觉模型版本选择/管理——vision hub 职责；ANP 不做 CV 模型版本管理。",
        command_type=None,
        implemented=False,
        target_capability="video_model",
    ),
}


def get_command_module(key: str) -> CommandModule | None:
    """按 key 取命令模块声明（未知返回 None）。"""

    return VIDEO_COMMAND_MODULES.get(key)


def list_command_modules() -> list[CommandModule]:
    """全部命令模块声明（前端枚举用，含占位）。"""

    return list(VIDEO_COMMAND_MODULES.values())
