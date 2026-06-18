"""智能体 registry（docs/architecture.md §3、gateway-api.md §1.1/§3）。

注册 / 心跳 / 在线降级离线 + 命令目标白名单。纯逻辑在 :class:`Registry`（可单测），
Kafka 接入在 :class:`RegistryConsumer`（网关后台线程用）。
"""

from __future__ import annotations

from .constants import (
    DEFAULT_AGENTS,
    HEARTBEAT_OFFLINE_TTL_SEC,
    HEARTBEAT_ONLINE_TTL_SEC,
)
from .models import AgentRecord, DerivedStatus
from .registry import CommandAuthz, Registry, seed_default_registry
from .service import RegistryConsumer, build_registry_consumer

__all__ = [
    "Registry",
    "CommandAuthz",
    "seed_default_registry",
    "AgentRecord",
    "DerivedStatus",
    "RegistryConsumer",
    "build_registry_consumer",
    "DEFAULT_AGENTS",
    "HEARTBEAT_ONLINE_TTL_SEC",
    "HEARTBEAT_OFFLINE_TTL_SEC",
]
