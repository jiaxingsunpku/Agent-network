"""网关运行时状态容器 —— 把读模型依赖聚到一处，便于注入与单测。

持有：World Status 当前态（只读消费状态层得来）、registry、静态拓扑、命令日志、
trend 环形缓冲、命令发布 producer。映射逻辑（snapshot/projection）是纯函数，读这里的
数据；HTTP 层只做编排。网关**不算世界状态**（AGENTS.md §3.4）。

测试可注入 producer=None（命令发布走 503）或 FakeProducer（记录发送、不连 Kafka）。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone

from ..contracts import Envelope, TrafficTopics
from ..messaging import publish
from ..registry import Registry, seed_default_registry
from ..system_agent import LatestStatusStore
from .command_log import CommandLog
from .config import TREND_CAPACITY, GatewayConfig
from .models import TrendPoint
from .topology import DEFAULT_TOPOLOGY, Topology


class PublishUnavailable(Exception):
    """无可用 producer（Kafka 不可用）→ HTTP 503。"""


class PublishFailed(Exception):
    """发布过程中 broker 报错 → HTTP 500。"""


class GatewayState:
    """网关读模型 + 命令发布所需的全部运行时依赖。"""

    def __init__(
        self,
        *,
        config: GatewayConfig | None = None,
        status_store: LatestStatusStore | None = None,
        registry: Registry | None = None,
        topology: Topology = DEFAULT_TOPOLOGY,
        command_log: CommandLog | None = None,
        producer=None,
    ) -> None:
        self.config = config or GatewayConfig()
        self.status_store = status_store or LatestStatusStore()
        self.registry = registry or seed_default_registry()
        self.topology = topology
        self.command_log = command_log or CommandLog()
        self.producer = producer
        self.command_topic = TrafficTopics.COMMAND
        self._trend_lock = threading.Lock()
        self._trend: deque[TrendPoint] = deque(maxlen=TREND_CAPACITY)
        self._trend_t = 0

    # -- 时间（可被测试覆盖）---------------------------------------------- #
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    # -- trend 环形缓冲 ---------------------------------------------------- #
    def append_trend(self, value: float) -> None:
        with self._trend_lock:
            self._trend.append(TrendPoint(t=float(self._trend_t), value=float(value)))
            self._trend_t += 1

    def trend(self) -> list[TrendPoint]:
        with self._trend_lock:
            return list(self._trend)

    # -- 命令发布 --------------------------------------------------------- #
    def publish_command(self, env: Envelope, *, timeout: float = 5.0) -> str:
        """发布命令 envelope 到控制层 topic，返回 message_id。

        无 producer → :class:`PublishUnavailable`（503）；broker 报错 →
        :class:`PublishFailed`（500）。网关不伪造 ack（protocol.md §5）。
        """

        if self.producer is None:
            raise PublishUnavailable("Kafka producer 不可用")
        try:
            future = publish(self.producer, self.command_topic, env, flush=True)
            future.get(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - kafka 异常类型较多，统一转 500
            raise PublishFailed(str(exc)) from exc
        return env.message_id
