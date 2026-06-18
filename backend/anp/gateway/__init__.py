"""网关包（docs/gateway-api.md、architecture.md §4）。

纯读模型 + 命令入口：读 World Status 当前态 + registry 拼 snapshot/projection，
校验并发布下行命令。对外只暴露 :func:`create_app` / :class:`GatewayState` /
:class:`GatewayConsumers` 与映射函数；不要深入子模块手搓响应。
"""

from __future__ import annotations

from .app import API_PREFIX, create_app
from .config import GatewayConfig
from .consumers import GatewayConsumers
from .mapping import build_projection, build_snapshot
from .state import GatewayState, PublishFailed, PublishUnavailable
from .topology import DEFAULT_TOPOLOGY, Topology

__all__ = [
    "create_app",
    "API_PREFIX",
    "GatewayState",
    "GatewayConfig",
    "GatewayConsumers",
    "PublishUnavailable",
    "PublishFailed",
    "build_snapshot",
    "build_projection",
    "DEFAULT_TOPOLOGY",
    "Topology",
]
