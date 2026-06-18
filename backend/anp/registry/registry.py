"""Registry 内存存储 —— 注册 / 心跳 / 下线 + 命令目标白名单。

线程安全：网关后台消费线程（lifecycle/heartbeat）写、HTTP 请求线程读，均加锁。
派生在线状态在读时按当前时间计算，不存周期性扫描线程（docs/gateway-api.md §1.1、
AGENTS.md §3.5：白名单——谁能发哪些命令——由 registry 统一裁决）。

时间一律传 tz-aware datetime；测试可注入 ``now`` 复现降级/离线判定。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import ValidationError

from ..contracts import (
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Envelope,
    EventType,
    parse_payload,
)
from .constants import DEFAULT_AGENTS
from .models import AgentRecord, DerivedStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CommandAuthz:
    """命令目标白名单裁决结果（与 docs/gateway-api.md §3 的错误码对应）。"""

    __slots__ = ("allowed", "code", "message")

    def __init__(self, allowed: bool, code: str | None = None, message: str | None = None) -> None:
        self.allowed = allowed
        self.code = code
        self.message = message


class Registry:
    """``agent_id -> AgentRecord`` 的并发安全 registry。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, AgentRecord] = {}

    # -- 写入 -------------------------------------------------------------- #
    def register(
        self,
        *,
        agent_id: str,
        agent_type: str,
        capabilities: list[str] | None = None,
        command_types: list[str] | None = None,
        now: datetime | None = None,
    ) -> AgentRecord:
        """注册或更新一个智能体（幂等：重复注册刷新能力/命令类型，保留心跳）。"""

        ts = now or _utcnow()
        with self._lock:
            existing = self._agents.get(agent_id)
            record = AgentRecord(
                agent_id=agent_id,
                agent_type=agent_type,
                capabilities=list(capabilities or []),
                command_types=list(command_types or []),
                registered_at=existing.registered_at if existing else ts,
                reported_status=existing.reported_status if existing else None,
                last_heartbeat_ts=existing.last_heartbeat_ts if existing else None,
                last_error=existing.last_error if existing else None,
            )
            self._agents[agent_id] = record
            return record

    def heartbeat(
        self,
        *,
        agent_id: str,
        status: str,
        last_error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """记录一次心跳。未注册的 agent 自动登记一条最小记录（容忍乱序：先心跳后 lifecycle）。"""

        ts = now or _utcnow()
        with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                record = AgentRecord(
                    agent_id=agent_id, agent_type="unknown", registered_at=ts
                )
            record = record.model_copy(
                update={"reported_status": status, "last_heartbeat_ts": ts, "last_error": last_error}
            )
            self._agents[agent_id] = record

    def deregister(self, agent_id: str) -> None:
        """下线：保留记录但标记 offline（保留拓扑里的节点，状态显示离线）。"""

        with self._lock:
            record = self._agents.get(agent_id)
            if record is None:
                return
            self._agents[agent_id] = record.model_copy(
                update={"reported_status": DerivedStatus.OFFLINE.value}
            )

    # -- 从 Kafka envelope 应用（lifecycle / heartbeat 共享 envelope）------- #
    def apply_envelope(self, env: Envelope, *, now: datetime | None = None) -> bool:
        """按 event_type 把一条 lifecycle/heartbeat envelope 应用到 registry。

        返回是否被处理（非法 payload / 无关类型返回 False，由调用方计数）。
        """

        try:
            payload = parse_payload(env)
        except ValidationError:
            return False

        if env.event_type == EventType.AGENT_REGISTERED and isinstance(payload, AgentLifecyclePayload):
            self.register(
                agent_id=payload.agent_id,
                agent_type=payload.agent_type,
                capabilities=payload.capabilities,
                command_types=payload.command_types,
                now=now,
            )
            return True
        if env.event_type == EventType.AGENT_DEREGISTERED and isinstance(payload, AgentLifecyclePayload):
            self.deregister(payload.agent_id)
            return True
        if env.event_type == EventType.AGENT_HEARTBEAT and isinstance(payload, AgentHeartbeatPayload):
            self.heartbeat(
                agent_id=env.source.agent_id,
                status=payload.status,
                last_error=payload.last_error,
                now=now,
            )
            return True
        return False

    # -- 读取 -------------------------------------------------------------- #
    def get(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            return self._agents.get(agent_id)

    def all(self) -> list[AgentRecord]:
        with self._lock:
            return list(self._agents.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._agents)

    # -- 命令目标白名单（docs/gateway-api.md §3、protocol.md §7）----------- #
    def authorize_command(self, agent_id: str, command_type: str) -> CommandAuthz:
        """裁决「能否向 agent_id 下发 command_type」。

        - agent 不在 registry → 403 ``target_not_whitelisted``。
        - agent 不接收该命令类型 → 403 ``command_not_allowed_for_target``。
        命令类型本身是否为合法枚举由网关在更前面校验（400）。
        """

        with self._lock:
            record = self._agents.get(agent_id)
        if record is None:
            return CommandAuthz(False, "target_not_whitelisted", f"未知命令目标: {agent_id}")
        if not record.accepts_command(command_type):
            return CommandAuthz(
                False,
                "command_not_allowed_for_target",
                f"{agent_id} 不接收命令类型 {command_type}",
            )
        return CommandAuthz(True)


def seed_default_registry(registry: Registry | None = None, *, now: datetime | None = None) -> Registry:
    """用 v1 默认智能体种子初始化 registry（无 live 心跳也能渲染节点）。"""

    reg = registry or Registry()
    for spec in DEFAULT_AGENTS:
        reg.register(
            agent_id=spec["agent_id"],
            agent_type=spec["agent_type"],
            capabilities=spec["capabilities"],
            command_types=spec["command_types"],
            now=now,
        )
    return reg
