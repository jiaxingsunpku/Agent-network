"""统一世界平台地基：接入方 SDK（WorldClient）+ model 抽象（ModelSpec / ModelRuntime）。

域无关——交通 / 视频 / 下一个域都用同一套原语接入世界（自注册带通道、声明 model、
跑工作流），而不是各搭并行栈。
"""

from __future__ import annotations

from .client import WorldClient
from .runtime import ModelRuntime
from .spec import ModelSpec, load_model_spec

__all__ = ["WorldClient", "ModelRuntime", "ModelSpec", "load_model_spec"]
