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
    Channel,
    Envelope,
    EventType,
    parse_iso,
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
        produces: list[Channel] | None = None,
        consumes: list[Channel] | None = None,
        weight: float | None = None,
        members: list[str] | None = None,
        now: datetime | None = None,
    ) -> AgentRecord:
        """注册或更新一个智能体（幂等：重复注册刷新能力/命令类型/通道/成员，保留心跳）。

        通道（produces/consumes）、weight、members 未传（None）时保留已有值，容忍只刷新
        部分字段的重复注册。
        """

        ts = now or _utcnow()
        with self._lock:
            existing = self._agents.get(agent_id)
            # Transition readers consume both world lifecycle and older traffic lifecycle.
            # Older traffic records may carry empty channel lists, so an empty
            # update must not erase a richer world registration for the same agent.
            next_produces = list(produces) if produces else (existing.produces if existing else [])
            next_consumes = list(consumes) if consumes else (existing.consumes if existing else [])
            record = AgentRecord(
                agent_id=agent_id,
                agent_type=agent_type,
                capabilities=list(capabilities or []),
                command_types=list(command_types or []),
                produces=next_produces,
                consumes=next_consumes,
                weight=weight if weight is not None else (existing.weight if existing else 1.0),
                members=list(members) if members is not None else (existing.members if existing else []),
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

        # 时间戳取事件时间（emission），而非处理时间：这样 registry 用 earliest 从头
        # 重放历史心跳重建世界视图时，按真实新鲜度判定，不会把旧心跳误算成「刚在线」。
        # 测试可注入 now 覆盖。
        ts = now or parse_iso(env.time.event_ts)

        if env.event_type == EventType.AGENT_REGISTERED and isinstance(payload, AgentLifecyclePayload):
            self.register(
                agent_id=payload.agent_id,
                agent_type=payload.agent_type,
                capabilities=payload.capabilities,
                command_types=payload.command_types,
                produces=payload.produces,
                consumes=payload.consumes,
                weight=payload.weight,
                members=payload.members,
                now=ts,
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
                now=ts,
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

    # -- 目录 / 发现（catalog 由 produces/consumes 投影，无需单独 topic）------ #
    def catalog_by_topic(self) -> dict[str, dict]:
        """按 topic 反查谁产/谁消，含 per-key 细分。

        返回 ``{topic: {"producers": [...], "consumers": [...],
        "keys": {key: {"producers": [...], "consumers": [...]}}}}``。
        keys 仅含 agent 显式声明覆盖的实体；通道未声明 keys（整条 topic）不进 keys 细分。
        """

        with self._lock:
            records = list(self._agents.values())

        catalog: dict[str, dict] = {}

        def _entry(topic: str) -> dict:
            return catalog.setdefault(topic, {"producers": [], "consumers": [], "keys": {}})

        def _key_entry(entry: dict, key: str) -> dict:
            return entry["keys"].setdefault(key, {"producers": [], "consumers": []})

        def _add(agent_id: str, channels: list[Channel], role: str) -> None:
            for ch in channels:
                entry = _entry(ch.topic)
                if agent_id not in entry[role]:
                    entry[role].append(agent_id)
                for key in ch.keys:
                    ke = _key_entry(entry, key)
                    if agent_id not in ke[role]:
                        ke[role].append(agent_id)

        for rec in records:
            _add(rec.agent_id, rec.produces, "producers")
            _add(rec.agent_id, rec.consumes, "consumers")
        return catalog

    def agents_covering(self, topic: str, key: str | None = None) -> list[AgentRecord]:
        """返回在 ``topic`` 上有通道的 agent；给 ``key`` 时只取覆盖该实体的。

        通道未声明 keys（整条 topic）视为覆盖任意 key。
        """

        with self._lock:
            records = list(self._agents.values())
        out: list[AgentRecord] = []
        for rec in records:
            for ch in list(rec.produces) + list(rec.consumes):
                if ch.topic != topic:
                    continue
                if key is None or not ch.keys or key in ch.keys:
                    out.append(rec)
                    break
        return out

    def agents_with_capability(self, capability: str) -> list[AgentRecord]:
        """返回 capabilities 含 ``capability`` 的 agent（model 选成员/发现用）。"""

        with self._lock:
            records = list(self._agents.values())
        return [rec for rec in records if capability in rec.capabilities]

    def models(self) -> list[AgentRecord]:
        """返回所有 model（``agent_type == "model"``）—— 左侧「自发现 model 列表」用。"""

        with self._lock:
            records = list(self._agents.values())
        return [rec for rec in records if rec.agent_type == "model"]

    def governed_by(self, agent_id: str) -> list[str]:
        """返回管辖 ``agent_id`` 的 model_id 列表（按 members 反向索引；共享成员可多个）。"""

        with self._lock:
            records = list(self._agents.values())
        return [rec.agent_id for rec in records if rec.agent_type == "model" and agent_id in rec.members]

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
            produces=spec.get("produces"),
            consumes=spec.get("consumes"),
            now=now,
        )
    return reg
