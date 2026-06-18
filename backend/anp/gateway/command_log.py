"""命令 / ack 的内存环形日志 —— 驱动 projection「命令闭环」tab 与 snapshot 事件。

网关发布命令时记一条 ``CommandEntry``；后台消费 ack topic 时按 ``command_id`` 回填
最近一次 ack 状态。这是审计账本的轻量内存形态：**不存 token、不存完整 payload**
（只留命令类型/目标/对象/状态/时间，符合 docs/gateway-api.md §3）。

线程安全：发布线程写、ack 消费线程写、HTTP 读线程读，统一加锁。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from .config import COMMAND_LOG_CAPACITY


class AckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    decision: str | None = None
    reason: str | None = None
    time: str


class CommandEntry(BaseModel):
    """一条命令及其最近 ack（不含完整 payload，只留审计必要字段）。"""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    command_type: str
    target_agent_id: str
    object_id: str | None = None
    region_id: str | None = None
    issued_at: str
    status: str = "published"   # published → 后续被 ack 覆盖为 accepted/completed/...
    ack: AckRecord | None = None


def _now_iso() -> str:
    from ..contracts import iso_utc

    return iso_utc(datetime.now(timezone.utc))


class CommandLog:
    """按 ``command_id`` 索引的环形命令日志（容量上限，超出淘汰最旧）。"""

    def __init__(self, capacity: int = COMMAND_LOG_CAPACITY) -> None:
        self._lock = threading.Lock()
        self._capacity = capacity
        self._entries: "OrderedDict[str, CommandEntry]" = OrderedDict()

    def record_command(
        self,
        *,
        command_id: str,
        command_type: str,
        target_agent_id: str,
        object_id: str | None = None,
        region_id: str | None = None,
        issued_at: str | None = None,
    ) -> CommandEntry:
        entry = CommandEntry(
            command_id=command_id,
            command_type=command_type,
            target_agent_id=target_agent_id,
            object_id=object_id,
            region_id=region_id,
            issued_at=issued_at or _now_iso(),
        )
        with self._lock:
            self._entries[command_id] = entry
            self._entries.move_to_end(command_id)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
        return entry

    def record_ack(
        self,
        *,
        command_id: str,
        command_type: str,
        status: str,
        decision: str | None = None,
        reason: str | None = None,
        target_agent_id: str | None = None,
        ack_time: str | None = None,
    ) -> None:
        """回填 ack。若命令未在本网关记录过（如别处发的命令）也建一条占位条目。"""

        ack = AckRecord(status=status, decision=decision, reason=reason, time=ack_time or _now_iso())
        with self._lock:
            entry = self._entries.get(command_id)
            if entry is None:
                entry = CommandEntry(
                    command_id=command_id,
                    command_type=command_type,
                    target_agent_id=target_agent_id or "unknown",
                    issued_at=ack.time,
                    status=status,
                    ack=ack,
                )
            else:
                entry = entry.model_copy(update={"status": status, "ack": ack})
            self._entries[command_id] = entry
            self._entries.move_to_end(command_id)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def recent(self, limit: int = 20) -> list[CommandEntry]:
        with self._lock:
            items = list(self._entries.values())
        return list(reversed(items))[:limit]

    def for_agent(self, agent_id: str, limit: int = 20) -> list[CommandEntry]:
        return [e for e in self.recent(limit=self._capacity) if e.target_agent_id == agent_id][:limit]

    def for_object(self, object_id: str, limit: int = 20) -> list[CommandEntry]:
        return [e for e in self.recent(limit=self._capacity) if e.object_id == object_id][:limit]
