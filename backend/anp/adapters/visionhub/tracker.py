"""命令对账表（command_log）—— 记「已发 → 收到结果」（P8）。

P8 决策：**不新增强制 ack topic**，用 ``command_id``（= vision hub ``trace.correlation_id``）
把下行命令与回流文本关联（phases/P8.md）。命令桥发出时 :meth:`mark_dispatched`，结果桥收到
对应 correlation 时 :meth:`mark_returned`。线程安全，供双桥共享 + 脚本/冒烟读账。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from anp.contracts import now_iso


@dataclass
class CommandRecord:
    """单条命令的下行/回流状态。"""

    command_id: str
    dispatched_ts: str
    camera_id: str | None = None
    road_name: str | None = None
    returned: bool = False
    returned_event_id: str | None = None
    returned_ts: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class CommandTracker:
    """``command_id`` → :class:`CommandRecord` 的线程安全对账表。"""

    def __init__(self) -> None:
        self._records: dict[str, CommandRecord] = {}
        self._lock = threading.Lock()

    def mark_dispatched(
        self, command_id: str, *, camera_id: str | None = None, road_name: str | None = None, **meta: Any
    ) -> CommandRecord:
        with self._lock:
            rec = self._records.get(command_id)
            if rec is None:
                rec = CommandRecord(
                    command_id=command_id,
                    dispatched_ts=now_iso(),
                    camera_id=camera_id,
                    road_name=road_name,
                    meta=dict(meta),
                )
                self._records[command_id] = rec
            return rec

    def mark_returned(self, command_id: str | None, event_id: str | None) -> bool:
        """标记某命令已收到回流文本。返回该命令是否在账（关联成功）。

        未在账（如重启后丢失下行记录，或非本桥发起）也补登一条「孤儿回流」，便于审计。
        """

        if not command_id:
            return False
        with self._lock:
            rec = self._records.get(command_id)
            known = rec is not None
            if rec is None:
                rec = CommandRecord(command_id=command_id, dispatched_ts=now_iso())
                self._records[command_id] = rec
            rec.returned = True
            rec.returned_event_id = event_id
            rec.returned_ts = now_iso()
            return known

    def get(self, command_id: str) -> CommandRecord | None:
        with self._lock:
            return self._records.get(command_id)

    def snapshot(self) -> list[CommandRecord]:
        with self._lock:
            return list(self._records.values())

    @property
    def dispatched(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def returned_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if r.returned)
