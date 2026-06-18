"""SignalVision 信号控制**执行侧**（P6，``traffic-exec-sv-001``）。

订阅控制层命令 → 去重 / 过期 / 目标匹配 / 本地 Safety Guard → 调真实 SV
``POST /api/junctions/<id>/update`` 写 ``traffic_light`` → 回 ack。处理顺序遵循
docs/protocol.md §5（与 ``agents/virtual_traffic.py:VirtualTrafficExecutor`` 一致，
把「目标匹配」上提到「去重」之前，避免别体 command_id 污染本体去重表）。

Safety Guard 的参数级规则（合法相位 / 时长区间）来自 :mod:`anp.control`（单一来源，
与虚拟体共用）；本执行体在其上叠加**路由约束**：命令 ``scope.object_id``（intersection_id）
必须映射到某个 SV junction，否则拒绝。

``handle_command`` 是纯逻辑（client 可注入桩）、可单测；``run`` 负责 Kafka 循环。
**不算 World Status**（共识指标一律由系统级智能体算，AGENTS.md §3.2）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from anp.contracts import (
    AckPayload,
    AckStatus,
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    CommandPayload,
    CommandType,
    Envelope,
    EventType,
    SafetyDecision,
    SequenceGenerator,
    Source,
    SourceSystem,
    TrafficTopics,
    ack_envelope,
    make_envelope,
    parse_iso,
    parse_payload,
)
from anp.control import signal_plan_safety_decision
from anp.messaging import make_consumer, publish

from .client import SignalVisionClient
from .config import SignalVisionExecConfig

#: 执行体声明的能力与可接收命令类型（与 lifecycle 注册一致）。
EXEC_CAPABILITIES = ("exec",)
EXEC_COMMAND_TYPES = (CommandType.SET_SIGNAL_PLAN.value,)


class SignalVisionExecutor:
    """SignalVision 信号控制执行端：消费命令、跑本地 Safety Guard、调 SV 写端点、回 ack。"""

    def __init__(
        self,
        config: SignalVisionExecConfig | None = None,
        *,
        client: SignalVisionClient | None = None,
    ) -> None:
        self.config = config or SignalVisionExecConfig()
        self.client = client or SignalVisionClient(
            self.config.sv_base_url, timeout_sec=self.config.http_timeout_sec
        )
        #: 平台 intersection_id → SV junction_id（执行时反查目标 junction）。
        self._intersection_to_junction = {v: k for k, v in self.config.junction_map.items()}
        self._seen: set[str] = set()
        self._seq = SequenceGenerator()
        #: 最近一次成功下发的信号配时（演示 / 冒烟断言用）。
        self.applied_plan: dict | None = None
        self.processed = 0
        self.ignored = 0
        self.dropped_invalid = 0
        self.rejected = 0
        self.failed = 0

    # -- 本地 Safety Guard（参数级 + 路由）-------------------------------- #
    def safety_guard(self, payload: CommandPayload) -> SafetyDecision:
        """参数级规则（共享自 :mod:`anp.control`）。路由约束在 :meth:`_resolve_junction`。"""

        return signal_plan_safety_decision(payload)

    def _resolve_junction(self, intersection_id: str | None) -> str | None:
        """平台 intersection_id → SV junction_id；未映射返回 ``None``。"""

        if intersection_id is None:
            return None
        return self._intersection_to_junction.get(intersection_id)

    # -- 执行：映射 → 调 SV 写端点 ---------------------------------------- #
    def _apply(self, junction_id: str, intersection_id: str, payload: CommandPayload) -> None:
        """把 set_signal_plan 映射成 SV traffic_light 并 POST；失败抛 RuntimeError（→ ack FAILED）。"""

        params = payload.params or {}
        desired_phase = params.get("desired_phase")
        duration_s = params.get("duration_s")
        # desired_phase / duration_s 已过 Safety Guard：相位合法、时长为区间内数值。
        phase_state = self.config.phase_state_map.get(desired_phase, desired_phase)
        traffic_light = {
            "phase_state": phase_state,        # 目标相位
            "phase_duration": 0.0,             # 新设相位，已持续 0s
            "next_switch_time": float(duration_s),  # duration_s 秒后切换
        }
        resp = self.client.update_junction(junction_id, traffic_light=traffic_light)
        if not resp.ok:
            raise RuntimeError(
                f"SV update 失败 junction={junction_id} status={resp.status_code} body={resp.body}"
            )
        self.applied_plan = {
            "intersection_id": intersection_id,
            "junction_id": junction_id,
            "traffic_light": traffic_light,
            "params": dict(params),
        }

    def _ack(self, payload: CommandPayload, status: AckStatus, safety: SafetyDecision | None = None) -> AckPayload:
        return AckPayload(
            command_id=payload.command_id,
            command_type=payload.command_type,
            status=status,
            safety=safety,
        )

    # -- 纯逻辑：单条命令 → ack（None 表示忽略，不回 ack）------------------ #
    def handle_command(self, env: Envelope, *, now: datetime | None = None) -> AckPayload | None:
        if env.event_type != EventType.COMMAND:
            return None
        # 目标匹配（上提）：非本体命令直接忽略。
        if env.target.agent_id != self.config.agent_id:
            self.ignored += 1
            return None
        try:
            payload = parse_payload(env)
        except Exception:  # noqa: BLE001 - 非法命令外形无 command_id 无法回 ack，计数丢弃
            self.dropped_invalid += 1
            return None
        assert isinstance(payload, CommandPayload)
        cid = payload.command_id

        # 去重。
        if cid in self._seen:
            return self._ack(payload, AckStatus.DUPLICATE)

        # 过期。
        now = now or datetime.now(timezone.utc)
        if env.time.expires_at and parse_iso(env.time.expires_at) < now:
            self._seen.add(cid)
            return self._ack(payload, AckStatus.EXPIRED)

        # 本地 Safety Guard（参数级）。
        decision = self.safety_guard(payload)
        if not decision.allowed:
            self._seen.add(cid)
            self.rejected += 1
            return self._ack(payload, AckStatus.REJECTED, safety=decision)

        # 路由约束：object_id（intersection_id）须映射到某 SV junction。
        intersection_id = env.scope.object_id
        junction_id = self._resolve_junction(intersection_id)
        if junction_id is None:
            self._seen.add(cid)
            self.rejected += 1
            route = SafetyDecision(
                allowed=False,
                decision="reject",
                reason=f"object_id 未映射到 SV junction（map={self.config.junction_map}）: {intersection_id!r}",
            )
            return self._ack(payload, AckStatus.REJECTED, safety=route)

        # 执行：调 SV 写端点。
        try:
            self._apply(junction_id, intersection_id, payload)
        except Exception as exc:  # noqa: BLE001 - SV 不可达 / 返回失败 → FAILED
            self._seen.add(cid)
            self.failed += 1
            return self._ack(
                payload, AckStatus.FAILED, safety=SafetyDecision(allowed=True, decision="allow", reason=str(exc))
            )
        self._seen.add(cid)
        self.processed += 1
        return self._ack(payload, AckStatus.COMPLETED, safety=decision)

    # -- 发布 ack ---------------------------------------------------------- #
    def publish_ack(self, producer, ack: AckPayload, *, target_agent_id: str | None = None) -> None:
        env = ack_envelope(
            agent_id=self.config.agent_id,
            payload=ack,
            target_agent_id=target_agent_id,
            sequence=self._seq.next(),
        )
        publish(producer, TrafficTopics.ACK, env)

    # -- SV 可达性探测（心跳用）------------------------------------------- #
    def probe_sv(self) -> tuple[bool, str | None]:
        status = self.client.get_status()
        if status.ok:
            return True, None
        return False, str(status.body.get("message") or "SV API 不可达")

    # -- 去重表重建（重放本体既往 ack）------------------------------------ #
    def rebuild_dedup_from_acks(self, *, bootstrap: str | None = None, timeout_ms: int = 4000) -> int:
        """重放 ack topic，把本体此前已回执的 command_id 重新装入去重表。返回重建条数。"""

        consumer = make_consumer(
            TrafficTopics.ACK,
            group_id=None,
            bootstrap_servers=bootstrap,
            auto_offset_reset="earliest",
            consumer_timeout_ms=timeout_ms,
        )
        n = 0
        try:
            for record in consumer:
                try:
                    env = Envelope.model_validate(record.value)
                    if env.event_type != EventType.COMMAND_ACK or env.source.agent_id != self.config.agent_id:
                        continue
                    payload = parse_payload(env)
                except Exception:  # noqa: BLE001
                    continue
                assert isinstance(payload, AckPayload)
                if payload.command_id not in self._seen:
                    self._seen.add(payload.command_id)
                    n += 1
        finally:
            consumer.close()
        return n

    # -- Kafka 循环 -------------------------------------------------------- #
    def run(self, consumer, producer) -> None:
        """消费命令、回 ack 直到 consumer 迭代结束（live: 永不结束）。"""

        try:
            for record in consumer:
                try:
                    env = Envelope.model_validate(record.value)
                except Exception:  # noqa: BLE001
                    self.dropped_invalid += 1
                    continue
                ack = self.handle_command(env)
                if ack is not None:
                    self.publish_ack(producer, ack, target_agent_id=env.source.agent_id)
        finally:
            producer.flush()


# --------------------------------------------------------------------------- #
# lifecycle / heartbeat（执行体：capabilities=[exec], command_types=[set_signal_plan]）
# --------------------------------------------------------------------------- #
def _exec_source(agent_id: str) -> Source:
    return Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id)


def exec_lifecycle_envelope(
    *,
    agent_id: str,
    registered: bool,
    agent_type: str = "signalvision",
    sequence: int = 0,
) -> Envelope:
    """注册/下线 envelope（topic anp.traffic.agent.lifecycle.v1）。"""

    payload = AgentLifecyclePayload(
        agent_id=agent_id,
        agent_type=agent_type,
        capabilities=list(EXEC_CAPABILITIES),
        command_types=list(EXEC_COMMAND_TYPES),
    )
    return make_envelope(
        event_type=EventType.AGENT_REGISTERED if registered else EventType.AGENT_DEREGISTERED,
        source=_exec_source(agent_id),
        payload=payload,
        sequence=sequence,
    )


def exec_heartbeat_envelope(
    *, agent_id: str, status: str = "online", last_error: str | None = None, sequence: int = 0
) -> Envelope:
    """心跳 envelope（topic anp.traffic.agent.heartbeat.v1）。SV 可达 online，不可达 degraded。"""

    return make_envelope(
        event_type=EventType.AGENT_HEARTBEAT,
        source=_exec_source(agent_id),
        payload=AgentHeartbeatPayload(status=status, last_error=last_error),
        sequence=sequence,
    )
