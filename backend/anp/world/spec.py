"""ModelSpec —— 一个 model（工作流）的仓库声明式配置。

model = 预先编排好的工作流，治一个城市问题、管辖一个 agent 子集。今天 spec 是
仓库内的 **JSON** 声明文件（确定、好测；数据形态便于将来平移到 Kafka 上的 compacted
``anp.world.model.v1`` topic，实现运行时热扩边界）。

``subscribe_topics`` 留空时，由 :class:`~anp.world.runtime.ModelRuntime` 用 registry
按成员 agent 的 ``produces`` 求并集推导（成员产出即本 model 输入）。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ModelSpec(BaseModel):
    """一个 model 的声明式规格。``group_id`` 决定 model 的 consumer group（一 model 一 group）。"""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)
    #: 人读：治什么城市问题。
    problem: str = ""
    #: 管辖的成员 agent 子集（成员可热扩，今天改 JSON 即可）。
    member_agent_ids: list[str] = Field(default_factory=list)
    #: 显式订阅的 topic；留空则由成员 produces 推导（见 ModelRuntime）。
    subscribe_topics: list[str] = Field(default_factory=list)
    #: 本 model 产出的 topic。
    produce_topics: list[str] = Field(default_factory=list)
    #: workflow 引用名（由 run_model.py 映射到具体纯逻辑对象，如 "system_agent"）。
    workflow: str = Field(min_length=1)
    #: model 的协作权重（先开槽，暂不驱动逻辑）。
    weight: float = Field(default=1.0, ge=0.0)

    @property
    def group_id(self) -> str:
        """一个 model = 一个 consumer group（与别的 model / 网关互不抢 offset）。"""

        return f"anp-model-{self.model_id}"


def load_model_spec(path: str | Path) -> ModelSpec:
    """从 JSON 文件读一个 ModelSpec。"""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ModelSpec.model_validate(data)
