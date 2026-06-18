"""v1 虚拟交通智能体 —— 感知部分（``traffic-virtual-001``）。

按固定采样间隔（默认 2s）为某路口各进口方向生成并上报观测：本间隔**通过量**
``vehicle_count``、瞬时**滞留**``halting_count``、平均速度 ``mean_speed_mps``
（docs/world-status.md §2）。执行部分（接命令 + 本地 Safety Guard）留到 P3 命令闭环。

观测由一个可复现的合成交通模型生成：每方向每拍取一个「压力」值 p∈[0,1]，
压力越高→滞留越多、速度越低、通过量越少，使下游产出多样的拥堵等级。
不写状态、不算 World Status；只上报原始观测。
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from anp.contracts import (
    AckPayload,
    AckStatus,
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Approach,
    CommandPayload,
    CommandType,
    Direction,
    Envelope,
    EventType,
    ObservationPayload,
    Quality,
    SafetyDecision,
    SequenceGenerator,
    Source,
    SourceSystem,
    Target,
    TrafficTopics,
    ack_envelope,
    iso_utc,
    make_envelope,
    observation_envelope,
    parse_iso,
    parse_payload,
)
from anp.messaging import make_consumer, make_producer, publish

VIRTUAL_AGENT_ID = "traffic-virtual-001"
DEFAULT_INTERSECTION = "gg-xiongchu-minzu"
DEFAULT_INTERVAL_SEC = 2.0
DEFAULT_HEARTBEAT_SEC = 5.0

#: 该虚拟体声明的能力与可接收命令类型（与 registry 种子一致）。
AGENT_CAPABILITIES = ("perception", "exec")
AGENT_COMMAND_TYPES = (CommandType.SET_SIGNAL_PLAN.value,)

#: 执行端本地 Safety Guard 参数（set_signal_plan）。权威安全闭环在执行端（protocol.md §7）。
ALLOWED_SIGNAL_PHASES = ("north_south_green", "east_west_green", "all_red")
MIN_SIGNAL_DURATION_SEC = 5
MAX_SIGNAL_DURATION_SEC = 120

#: 自由流速度（m/s），与系统级 V_FREE_KMH=40 对齐（40/3.6）。
_V_FREE_MPS = 40.0 / 3.6


class VirtualTrafficAgent:
    """合成观测的感知体。``build_observation`` 纯生成、``run`` 负责定时发布。"""

    def __init__(
        self,
        *,
        agent_id: str = VIRTUAL_AGENT_ID,
        intersection_id: str = DEFAULT_INTERSECTION,
        directions: list[Direction] | None = None,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        seed: int | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.intersection_id = intersection_id
        self.directions = directions or list(Direction)
        self.interval_sec = interval_sec
        self._rng = random.Random(seed)
        self._seq = SequenceGenerator()
        # 每方向维持一个缓慢游走的压力，避免逐拍突变。
        self._pressure = {d: self._rng.uniform(0.2, 0.8) for d in self.directions}

    # -- 合成模型 ---------------------------------------------------------- #
    def _step_pressure(self, direction: Direction) -> float:
        p = self._pressure[direction] + self._rng.uniform(-0.15, 0.15)
        p = min(max(p, 0.0), 1.0)
        self._pressure[direction] = p
        return p

    def _approach(self, direction: Direction) -> Approach:
        p = self._step_pressure(direction)
        # 压力高→速度低（留 15% 自由流下限的噪声），滞留多，通过量少。
        # vehicle_count 是「本 2s 间隔通过量」：取较小整数，使换算后的进口流量落在
        # 现实量级（约几百~几千 veh/h），避免每 2s 上十辆导致流量虚高（×1800/拍）。
        speed = max(_V_FREE_MPS * (1.0 - 0.85 * p) + self._rng.uniform(-0.5, 0.5), 0.6)
        halting = max(int(round(p * 12 + self._rng.uniform(-1, 1))), 0)
        vehicles = max(int(round((1.0 - p) * 2.0 + self._rng.uniform(0, 1.0))), 0)
        return Approach(
            direction=direction,
            vehicle_count=vehicles,
            halting_count=halting,
            mean_speed_mps=round(speed, 2),
        )

    def build_observation(self) -> ObservationPayload:
        """生成一拍各方向观测（不带时间戳，由发布时填 event_ts）。"""

        return ObservationPayload(
            intersection_id=self.intersection_id,
            approaches=[self._approach(d) for d in self.directions],
        )

    # -- 发布 -------------------------------------------------------------- #
    def publish_once(self, producer, *, event_ts: str | None = None) -> str:
        """生成并发布一拍观测，返回所用 event_ts。"""

        payload = self.build_observation()
        ts = event_ts or iso_utc(datetime.now(timezone.utc))
        env = observation_envelope(
            agent_id=self.agent_id,
            payload=payload,
            event_ts=ts,
            sequence=self._seq.next(),
            quality=Quality(confidence=1.0),
        )
        publish(producer, TrafficTopics.OBSERVATION, env)
        return ts

    def run(self, *, duration_sec: float | None = None, bootstrap: str | None = None) -> int:
        """定时发布观测，直到 ``duration_sec`` 用尽（None=永久）。返回已发条数。"""

        producer = make_producer(bootstrap_servers=bootstrap)
        sent = 0
        start = time.monotonic()
        try:
            while duration_sec is None or (time.monotonic() - start) < duration_sec:
                ts = self.publish_once(producer)
                sent += 1
                print(f"[virtual] sent #{sent} intersection={self.intersection_id} event_ts={ts}")
                time.sleep(self.interval_sec)
        except KeyboardInterrupt:
            print("\n[virtual] 收到中断，停止上报。")
        finally:
            producer.flush()
            producer.close()
        return sent


# --------------------------------------------------------------------------- #
# lifecycle / heartbeat（注册、心跳、下线）
# --------------------------------------------------------------------------- #
def _agent_source(agent_id: str) -> Source:
    return Source(system=SourceSystem.COLLABORATIVE_AGENT, agent_id=agent_id)


def lifecycle_envelope(
    *, agent_id: str, registered: bool, agent_type: str = "virtual", sequence: int = 0
) -> Envelope:
    """注册/下线 envelope（topic anp.traffic.agent.lifecycle.v1）。"""

    payload = AgentLifecyclePayload(
        agent_id=agent_id,
        agent_type=agent_type,
        capabilities=list(AGENT_CAPABILITIES),
        command_types=list(AGENT_COMMAND_TYPES),
    )
    return make_envelope(
        event_type=EventType.AGENT_REGISTERED if registered else EventType.AGENT_DEREGISTERED,
        source=_agent_source(agent_id),
        payload=payload,
        sequence=sequence,
    )


def heartbeat_envelope(
    *, agent_id: str, status: str = "online", last_error: str | None = None, sequence: int = 0
) -> Envelope:
    """心跳 envelope（topic anp.traffic.agent.heartbeat.v1）。"""

    return make_envelope(
        event_type=EventType.AGENT_HEARTBEAT,
        source=_agent_source(agent_id),
        payload=AgentHeartbeatPayload(status=status, last_error=last_error),
        sequence=sequence,
    )


# --------------------------------------------------------------------------- #
# 执行部分：命令闭环（去重 → 过期 → 目标匹配 → 本地 Safety Guard → 执行 → ack）
# --------------------------------------------------------------------------- #
class VirtualTrafficExecutor:
    """``traffic-virtual-001`` 执行端：消费命令、跑本地 Safety Guard、回 ack。

    处理顺序遵循 docs/protocol.md §5，但把「目标匹配」上提到「去重」之前：共享命令
    topic 上，非本体命令应当**直接忽略**，不进本体去重表，避免污染（语义不变、更稳）。

    去重表为内存集合，但可由 :meth:`rebuild_dedup_from_acks` 重放本体既往 ack 重建
    （protocol.md §6：去重表应可重建，不要进程重启即丢）。``handle_command`` 是纯逻辑、
    可单测；``run`` 负责 Kafka 循环。
    """

    def __init__(self, *, agent_id: str = VIRTUAL_AGENT_ID) -> None:
        self.agent_id = agent_id
        self._seen: set[str] = set()
        self._seq = SequenceGenerator()
        #: 最近一次成功应用的信号配时（演示用，无真实设备）。
        self.applied_plan: dict | None = None
        self.processed = 0
        self.ignored = 0
        self.dropped_invalid = 0

    # -- 本地 Safety Guard ------------------------------------------------- #
    def safety_guard(self, payload: CommandPayload) -> SafetyDecision:
        """命令类型白名单 + 参数范围校验（set_signal_plan）。"""

        if payload.command_type != CommandType.SET_SIGNAL_PLAN:
            return SafetyDecision(
                allowed=False, decision="reject", reason=f"不支持的命令类型: {payload.command_type}"
            )
        params = payload.params or {}
        phase = params.get("desired_phase")
        if phase not in ALLOWED_SIGNAL_PHASES:
            return SafetyDecision(
                allowed=False,
                decision="reject",
                reason=f"desired_phase 非法（允许 {list(ALLOWED_SIGNAL_PHASES)}）: {phase!r}",
            )
        duration = params.get("duration_s")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return SafetyDecision(allowed=False, decision="reject", reason="duration_s 必须是数值")
        if not (MIN_SIGNAL_DURATION_SEC <= duration <= MAX_SIGNAL_DURATION_SEC):
            return SafetyDecision(
                allowed=False,
                decision="reject",
                reason=f"duration_s 须在 [{MIN_SIGNAL_DURATION_SEC}, {MAX_SIGNAL_DURATION_SEC}]: {duration}",
            )
        return SafetyDecision(allowed=True, decision="allow", reason="通过本地 Safety Guard")

    def _execute(self, payload: CommandPayload) -> None:
        """应用信号配时（v1 无真实设备：仅记录最近配时）。"""

        self.applied_plan = dict(payload.params)

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
        if env.target.agent_id != self.agent_id:
            self.ignored += 1
            return None
        try:
            payload = parse_payload(env)
        except Exception:  # noqa: BLE001 - 非法命令外形无法回 ack（无 command_id），计数丢弃
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

        # 本地 Safety Guard。
        decision = self.safety_guard(payload)
        if not decision.allowed:
            self._seen.add(cid)
            return self._ack(payload, AckStatus.REJECTED, safety=decision)

        # 执行。
        try:
            self._execute(payload)
        except Exception as exc:  # noqa: BLE001
            return self._ack(
                payload, AckStatus.FAILED, safety=SafetyDecision(allowed=True, decision="allow", reason=str(exc))
            )
        self._seen.add(cid)
        self.processed += 1
        return self._ack(payload, AckStatus.COMPLETED, safety=decision)

    # -- 发布 ack ---------------------------------------------------------- #
    def publish_ack(self, producer, ack: AckPayload, *, target_agent_id: str | None = None) -> None:
        env = ack_envelope(
            agent_id=self.agent_id,
            payload=ack,
            target_agent_id=target_agent_id,
            sequence=self._seq.next(),
        )
        publish(producer, TrafficTopics.ACK, env)

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
                    if env.event_type != EventType.COMMAND_ACK or env.source.agent_id != self.agent_id:
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
