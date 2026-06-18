"""按 event_ts 的滚动窗口缓冲（tumbling）—— 纯逻辑，不依赖 Kafka。

每路口独立切桶；窗口边界对齐 epoch：第 ``k`` 个窗口覆盖 ``[k·W, (k+1)·W)``。
窗口在 ``window_end + GRACE`` 被「水位线」（该路口已见的最大 event_ts）越过时关闭结算；
结算后再来、落在已关闭窗口的观测直接丢弃并计数（docs/world-status.md §1、protocol.md §2）。

只负责「何时关窗、关窗时带哪些观测」；具体指标计算见 compute.py。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import floor

from ..contracts import ObservationPayload
from .constants import GRACE_SEC, WINDOW_SIZE_SEC


@dataclass
class ClosedWindow:
    """一个已关闭、可结算的窗口及其窗口内观测。"""

    intersection_id: str
    start: datetime  # 窗口起（含），UTC
    end: datetime  # 窗口止（不含），UTC
    observations: list[ObservationPayload] = field(default_factory=list)

    @property
    def sample_count(self) -> int:
        return len(self.observations)


class WindowAggregator:
    """事件时间水位驱动的滚动窗口缓冲。

    用法：每条观测调用 :meth:`add`，返回因本条 event_ts 推高水位而关闭的窗口列表；
    有限流末尾调用 :meth:`flush_all` 把残留窗口全部结算。
    """

    def __init__(self, window_size_sec: int = WINDOW_SIZE_SEC, grace_sec: int = GRACE_SEC) -> None:
        self._size = window_size_sec
        self._grace = timedelta(seconds=grace_sec)
        # intersection_id -> {window_index -> [observations]}
        self._buckets: dict[str, dict[int, list[ObservationPayload]]] = defaultdict(dict)
        # intersection_id -> 已见最大 event_ts（水位线）
        self._watermark: dict[str, datetime] = {}
        # intersection_id -> 已关闭的最大窗口下标（用于丢弃迟到）
        self._last_closed: dict[str, int] = {}
        #: 因迟到（落在已关闭窗口）被丢弃的观测数。
        self.dropped_late = 0

    # -- 窗口下标 / 边界 ---------------------------------------------------- #
    def _index(self, dt: datetime) -> int:
        return floor(dt.timestamp() / self._size)

    def _bounds(self, index: int) -> tuple[datetime, datetime]:
        start = datetime.fromtimestamp(index * self._size, tz=timezone.utc)
        end = datetime.fromtimestamp((index + 1) * self._size, tz=timezone.utc)
        return start, end

    # -- 主入口 ------------------------------------------------------------ #
    def add(self, intersection_id: str, event_ts: datetime, obs: ObservationPayload) -> list[ClosedWindow]:
        """缓冲一条观测，返回因水位推进而关闭的窗口（可能为空）。"""

        idx = self._index(event_ts)
        last_closed = self._last_closed.get(intersection_id)
        if last_closed is not None and idx <= last_closed:
            # 落在已结算窗口（或更早）——迟到，丢弃。
            self.dropped_late += 1
            return []

        self._buckets[intersection_id].setdefault(idx, []).append(obs)
        wm = self._watermark.get(intersection_id)
        if wm is None or event_ts > wm:
            self._watermark[intersection_id] = event_ts
        return self._close_ready(intersection_id)

    def _close_ready(self, intersection_id: str) -> list[ClosedWindow]:
        wm = self._watermark[intersection_id]
        buckets = self._buckets[intersection_id]
        ready_idx = []
        for idx in sorted(buckets):
            _, end = self._bounds(idx)
            if wm >= end + self._grace:
                ready_idx.append(idx)
        return self._pop_windows(intersection_id, ready_idx)

    def _pop_windows(self, intersection_id: str, indices: list[int]) -> list[ClosedWindow]:
        buckets = self._buckets[intersection_id]
        closed: list[ClosedWindow] = []
        for idx in indices:
            start, end = self._bounds(idx)
            closed.append(
                ClosedWindow(
                    intersection_id=intersection_id,
                    start=start,
                    end=end,
                    observations=buckets.pop(idx),
                )
            )
            prev = self._last_closed.get(intersection_id, idx)
            self._last_closed[intersection_id] = max(prev, idx)
        return closed

    def flush_all(self) -> list[ClosedWindow]:
        """结算所有路口的残留窗口（有限流末尾 / 关停时用），按 (路口, 窗口) 升序。"""

        out: list[ClosedWindow] = []
        for intersection_id in sorted(self._buckets):
            indices = sorted(self._buckets[intersection_id])
            out.extend(self._pop_windows(intersection_id, indices))
        return out
