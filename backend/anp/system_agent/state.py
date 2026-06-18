"""每路口最新 World Status 的内存当前态（docs/world-status.md §5）。

系统级智能体每结算一个窗口就 :meth:`update` 一次；网关（P3）直接读 :meth:`get`/
:meth:`all` 构建 snapshot/projection，无需回放 Kafka。线程安全：单消费线程写、
读多为同进程读，仍加锁以防 P3 把网关放到独立线程。
"""

from __future__ import annotations

import threading

from ..contracts import IntersectionStatusPayload


class LatestStatusStore:
    """``intersection_id -> 最新 IntersectionStatusPayload`` 的并发安全字典。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, IntersectionStatusPayload] = {}

    def update(self, status: IntersectionStatusPayload) -> None:
        with self._lock:
            self._latest[status.intersection_id] = status

    def get(self, intersection_id: str) -> IntersectionStatusPayload | None:
        with self._lock:
            return self._latest.get(intersection_id)

    def all(self) -> dict[str, IntersectionStatusPayload]:
        """返回当前态快照（浅拷贝外层 dict，值为不可变 payload）。"""

        with self._lock:
            return dict(self._latest)

    def __len__(self) -> int:
        with self._lock:
            return len(self._latest)
