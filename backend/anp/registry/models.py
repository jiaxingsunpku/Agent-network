"""Registry 数据模型 —— 智能体记录与派生在线状态。

记录的「上报状态」来自心跳 payload（online/degraded/offline 自由字符串），
而「派生状态」由记录的上报状态 + 心跳新鲜度共同决定，供网关映射成前端
NodeStatus（docs/gateway-api.md §1.1）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import Channel
from .constants import HEARTBEAT_OFFLINE_TTL_SEC, HEARTBEAT_ONLINE_TTL_SEC


class DerivedStatus(str, Enum):
    """由心跳新鲜度派生的智能体在线状态（网关再映射为前端 NodeStatus）。"""

    SYNCING = "syncing"   # 已注册但还没收到心跳
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class AgentRecord(BaseModel):
    """registry 中一条智能体记录。``last_heartbeat_ts`` 为 None 表示尚无心跳。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    command_types: list[str] = Field(default_factory=list)
    #: 统一世界通道声明（agent 在哪些 topic/实体上产/消，catalog/发现用）。
    produces: list[Channel] = Field(default_factory=list)
    consumes: list[Channel] = Field(default_factory=list)
    #: 协作权重，先开槽默认 1.0、暂不驱动逻辑。
    weight: float = Field(default=1.0, ge=0.0)
    registered_at: datetime
    #: 心跳 payload 里的自报状态（online/degraded/offline…），无心跳时为 None。
    reported_status: str | None = None
    last_heartbeat_ts: datetime | None = None
    last_error: str | None = None

    def derived_status(self, now: datetime) -> DerivedStatus:
        """按心跳新鲜度 + 自报状态推导在线状态。

        - 从未心跳 → ``syncing``。
        - 心跳新鲜（≤ ONLINE_TTL）→ 采用自报状态（degraded/offline 原样，其余视为 online）。
        - 心跳偏旧（≤ OFFLINE_TTL）→ ``degraded``。
        - 心跳过旧（> OFFLINE_TTL）→ ``offline``。
        """

        if self.last_heartbeat_ts is None:
            return DerivedStatus.SYNCING
        age = (now - self.last_heartbeat_ts).total_seconds()
        if age <= HEARTBEAT_ONLINE_TTL_SEC:
            reported = (self.reported_status or "online").lower()
            if reported in (DerivedStatus.DEGRADED.value, DerivedStatus.OFFLINE.value):
                return DerivedStatus(reported)
            return DerivedStatus.ONLINE
        if age <= HEARTBEAT_OFFLINE_TTL_SEC:
            return DerivedStatus.DEGRADED
        return DerivedStatus.OFFLINE

    def accepts_command(self, command_type: str) -> bool:
        return command_type in self.command_types
