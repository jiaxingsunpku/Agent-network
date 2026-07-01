"""读模型映射 —— World Status + registry + 静态拓扑 → snapshot / projection。

纯函数：输入 :class:`GatewayState` 的数据快照，输出契约模型（docs/gateway-api.md §1/§2）。
不碰 Kafka、不算世界状态（聚合在 P2 system_agent）。字段映射严格按 world-status.md §5：
``flow_veh_h→flow``、``mean_speed_kmh→speedKmh``、``mean_delay_sec→delaySec``、
``queue_length_m→queueM``、``congestion_level→state``。
"""

from __future__ import annotations

from datetime import datetime

from ..contracts import (
    CongestionLevel,
    IntersectionStatusPayload,
    iso_utc,
    parse_iso,
)
from ..registry import AgentRecord, DerivedStatus, Registry
from ..system_agent import LatestStatusStore, WINDOW_SIZE_SEC
from . import models as m
from .command_log import CommandEntry, CommandLog
from .config import GATEWAY_AGENT_ID, STATUS_STALE_SEC
from .topology import IntersectionSpec, Topology

# --------------------------------------------------------------------------- #
# 节点位置：路口来自拓扑；智能体/服务节点用稳定布局（presentational）。
# --------------------------------------------------------------------------- #
_AGENT_POSITIONS: dict[str, tuple[float, float]] = {
    "traffic-system-001": (-110.0, -170.0),
    "traffic-virtual-001": (110.0, -170.0),
}
_AGENT_FALLBACK_Y = -170.0

#: DerivedStatus → 前端 NodeStatus（degraded→warning）。
_AGENT_STATUS_MAP: dict[DerivedStatus, m.NodeStatus] = {
    DerivedStatus.ONLINE: "online",
    DerivedStatus.DEGRADED: "warning",
    DerivedStatus.OFFLINE: "offline",
    DerivedStatus.SYNCING: "syncing",
}
_AGENT_HEALTH_MAP: dict[DerivedStatus, int] = {
    DerivedStatus.ONLINE: 100,
    DerivedStatus.DEGRADED: 60,
    DerivedStatus.OFFLINE: 0,
    DerivedStatus.SYNCING: 80,
}


def _clamp_int(v: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(v))))


# --------------------------------------------------------------------------- #
# 路口节点（World Status → 前端指标 + 状态映射）
# --------------------------------------------------------------------------- #
def _status_age_sec(status: IntersectionStatusPayload, now: datetime) -> float:
    return (now - parse_iso(status.window.end)).total_seconds()


def _intersection_node_status(
    status: IntersectionStatusPayload | None, now: datetime
) -> m.NodeStatus:
    """World Status 新鲜度 + 拥堵度 → NodeStatus（docs/gateway-api.md §1.1）。"""

    if status is None:
        return "syncing"  # 拓扑已知但还没收到任何 World Status
    if _status_age_sec(status, now) > STATUS_STALE_SEC:
        return "offline"  # 超时未更新
    level = status.congestion_level
    if level in (CongestionLevel.SMOOTH, CongestionLevel.SLOW):
        return "online"
    if level == CongestionLevel.CONGESTED:
        return "warning"
    return "offline"  # 严重


def _intersection_metrics(status: IntersectionStatusPayload | None) -> dict:
    """映射成前端 HotIntersectionRuntime 风格的指标键（world-status.md §5）。"""

    if status is None:
        return {"flow": 0, "speedKmh": 0.0, "delaySec": 0.0, "queueM": 0.0, "state": "", "congestionIndex": 0.0}
    return {
        "flow": round(status.flow_veh_h),
        "speedKmh": round(status.mean_speed_kmh, 1),
        "delaySec": round(status.mean_delay_sec, 1),
        "queueM": round(status.queue_length_m, 1),
        "state": status.congestion_level.value,
        "congestionIndex": round(status.congestion_index, 3),
    }


def _intersection_node(spec: IntersectionSpec, store: LatestStatusStore, now: datetime) -> m.Node:
    status = store.get(spec.id)
    node_status = _intersection_node_status(status, now)
    health = 100 if status is None else _clamp_int(100 - status.congestion_index * 100)
    return m.Node(
        id=spec.id,
        label=spec.label,
        node_type="region",
        group="traffic",
        position=m.Position(x=spec.x, y=spec.y),
        status=node_status,
        health=health,
        tags=["traffic"],
        metrics=_intersection_metrics(status),
    )


# --------------------------------------------------------------------------- #
# 智能体节点（registry → 节点）
# --------------------------------------------------------------------------- #
def _agent_position(agent_id: str, index: int) -> m.Position:
    if agent_id in _AGENT_POSITIONS:
        x, y = _AGENT_POSITIONS[agent_id]
        return m.Position(x=x, y=y)
    return m.Position(x=-220.0 + index * 160.0, y=_AGENT_FALLBACK_Y)


def _agent_node(record: AgentRecord, now: datetime, index: int) -> m.Node:
    derived = record.derived_status(now)
    return m.Node(
        id=record.agent_id,
        label=record.agent_id,
        node_type="agent",
        group="agent",
        position=_agent_position(record.agent_id, index),
        status=_AGENT_STATUS_MAP[derived],
        health=_AGENT_HEALTH_MAP[derived],
        tags=list(record.capabilities),
        metrics={
            "agentType": record.agent_type,
            "status": derived.value,
            "capabilities": list(record.capabilities),
            "commandTypes": list(record.command_types),
            "lastHeartbeat": iso_utc(record.last_heartbeat_ts) if record.last_heartbeat_ts else None,
            "lastError": record.last_error,
        },
    )


# --------------------------------------------------------------------------- #
# 边 / 资源
# --------------------------------------------------------------------------- #
def _worse_status(a: m.NodeStatus, b: m.NodeStatus) -> m.NodeStatus:
    order = {"offline": 0, "warning": 1, "syncing": 2, "online": 3}
    return a if order[a] <= order[b] else b


def _build_edges(
    topology: Topology, node_status: dict[str, m.NodeStatus]
) -> list[m.Edge]:
    edges: list[m.Edge] = []
    # 路段边：状态取两端路口较差者。
    for road in topology.roads:
        s = node_status.get(road.source, "syncing")
        t = node_status.get(road.target, "syncing")
        edges.append(
            m.Edge(
                id=road.id,
                source=road.source,
                target=road.target,
                label=road.label,
                directed=road.directed,
                relation_type=road.relation_type,
                status=_worse_status(s, t),
                metrics={},
            )
        )
    # 智能体↔实体关系边：状态取源智能体状态。
    for src, dst, label, relation_type, directed in topology.agent_relations:
        edges.append(
            m.Edge(
                id=f"rel-{src}-{dst}",
                source=src,
                target=dst,
                label=label,
                directed=directed,
                relation_type=relation_type,
                status=node_status.get(src, "syncing"),
                metrics={},
            )
        )
    return edges


def _build_resources(topology: Topology, node_status: dict[str, m.NodeStatus]) -> list[m.Resource]:
    resources: list[m.Resource] = []
    for spec in topology.resources:
        resources.append(
            m.Resource(
                id=spec.id,
                label=spec.label,
                resource_type=spec.resource_type,  # type: ignore[arg-type]
                anchor_agent_id=spec.anchor_agent_id,
                height=spec.height,
                direction=spec.direction,  # type: ignore[arg-type]
                status=node_status.get(spec.anchor_agent_id, "online"),
                metrics={},
            )
        )
    return resources


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #
def _congestion_events(
    topology: Topology, store: LatestStatusStore, now: datetime, limit: int = 6
) -> list[m.Event]:
    events: list[m.Event] = []
    for spec in topology.intersections:
        st = store.get(spec.id)
        if st is None or _status_age_sec(st, now) > STATUS_STALE_SEC:
            continue
        if st.congestion_level == CongestionLevel.SEVERE:
            severity = "critical"
        elif st.congestion_level == CongestionLevel.CONGESTED:
            severity = "warning"
        else:
            continue
        events.append(
            m.Event(
                id=f"evt-cong-{spec.id}-{st.window.end}",
                severity=severity,  # type: ignore[arg-type]
                title=f"{spec.label} {st.congestion_level.value}（延误 {st.mean_delay_sec:.0f}s）",
                target_id=spec.id,
                time=st.window.end,
            )
        )
    return events[:limit]


def _command_events(command_log: CommandLog, limit: int = 6) -> list[m.Event]:
    events: list[m.Event] = []
    for entry in command_log.recent(limit=limit):
        status = entry.ack.status if entry.ack else entry.status
        severity = "warning" if status in ("rejected", "failed", "expired") else "info"
        events.append(
            m.Event(
                id=f"evt-cmd-{entry.command_id}",
                severity=severity,  # type: ignore[arg-type]
                title=f"命令 {entry.command_type} → {entry.target_agent_id} [{status}]",
                target_id=entry.target_agent_id,
                time=entry.ack.time if entry.ack else entry.issued_at,
            )
        )
    return events


# --------------------------------------------------------------------------- #
# snapshot 装配
# --------------------------------------------------------------------------- #
def build_snapshot(state, now: datetime | None = None) -> m.NetworkSnapshot:
    """组装 NetworkSnapshot（docs/gateway-api.md §1）。"""

    now = now or state.now()
    topology: Topology = state.topology
    store: LatestStatusStore = state.status_store
    registry: Registry = state.registry

    # 节点：路口（拓扑 + World Status）+ 智能体（registry）+ 网关服务节点。
    intersection_nodes = [_intersection_node(spec, store, now) for spec in topology.intersections]
    agents = sorted(registry.all(), key=lambda r: r.agent_id)
    agent_nodes = [_agent_node(rec, now, i) for i, rec in enumerate(agents)]
    gateway_node = m.Node(
        id=GATEWAY_AGENT_ID,
        label="网关读模型",
        node_type="service",
        group="platform",
        position=m.Position(x=0.0, y=-320.0),
        status="online",
        health=100,
        tags=["gateway", "read-model"],
        metrics={"role": "gateway", "endpoints": ["snapshot", "projection", "commands"]},
    )
    nodes = intersection_nodes + agent_nodes + [gateway_node]

    node_status = {n.id: n.status for n in nodes}
    edges = _build_edges(topology, node_status)
    resources = _build_resources(topology, node_status)

    # summary。
    healths = [n.health for n in nodes]
    healthy_percent = _clamp_int(sum(healths) / len(healths)) if healths else 100
    summary = m.Summary(
        agents=len(agent_nodes),
        relations=len(edges),
        resources=len(resources),
        healthy_percent=healthy_percent,
        kafka_lag_ms=0,  # v1 不测量
        update_rate=round(1.0 / WINDOW_SIZE_SEC, 3),  # World Status 产出频率（Hz）
    )

    events = _congestion_events(topology, store, now) + _command_events(state.command_log)

    return m.NetworkSnapshot(
        version="gateway",
        generated_at=iso_utc(now),
        topology_version=topology.topology_version,
        region=topology.region,
        summary=summary,
        nodes=nodes,
        edges=edges,
        resources=resources,
        trend=state.trend(),
        events=events,
    )


# --------------------------------------------------------------------------- #
# projection 装配（docs/gateway-api.md §2）
# --------------------------------------------------------------------------- #
def _kv(label: str, value) -> dict:
    return {"label": label, "value": value}


def _metric(label: str, value) -> dict:
    return {"label": label, "value": value}


def _command_event_items(entries: list[CommandEntry]) -> list[dict]:
    items = []
    for e in entries:
        status = e.ack.status if e.ack else e.status
        items.append(
            {
                "title": f"{e.command_type} → {e.target_agent_id}",
                "status": status,
                "time": e.ack.time if e.ack else e.issued_at,
                "reason": e.ack.reason if e.ack else None,
                "severity": "warning" if status in ("rejected", "failed", "expired") else "info",
            }
        )
    return items


def _intersection_projection(
    spec: IntersectionSpec, state, now: datetime
) -> m.InspectorProjection:
    st = state.status_store.get(spec.id)
    tabs: list[m.InspectorTab] = []

    # 当前态。
    if st is not None:
        status_blocks = [
            m.InspectorBlock(
                type="metric_grid",
                title="路口指标",
                items=[
                    _metric("排队(m)", round(st.queue_length_m, 1)),
                    _metric("流量(veh/h)", round(st.flow_veh_h)),
                    _metric("速度(km/h)", round(st.mean_speed_kmh, 1)),
                    _metric("延误(s)", round(st.mean_delay_sec, 1)),
                ],
            ),
            m.InspectorBlock(
                type="kv_list",
                title="拥堵",
                items=[
                    _kv("等级", st.congestion_level.value),
                    _kv("拥堵指数", round(st.congestion_index, 3)),
                    _kv("数据新鲜度(s)", round(_status_age_sec(st, now), 1)),
                ],
            ),
        ]
    else:
        status_blocks = [
            m.InspectorBlock(type="kv_list", title="当前态", items=[_kv("状态", "等待 World Status…")])
        ]
    tabs.append(m.InspectorTab(id="status", title="当前态", blocks=status_blocks))

    # 最近窗口。
    if st is not None:
        window_items = [
            _kv("窗口起", st.window.start),
            _kv("窗口止", st.window.end),
            _kv("窗口长(s)", st.window.size_sec),
            _kv("样本数", st.window.sample_count),
        ]
        approach_items = [
            _kv(a.direction.value, f"排队{a.queue_length_m:.0f}m / 流量{a.flow_veh_h:.0f} / 速度{a.mean_speed_kmh:.0f}km/h")
            for a in st.approaches
        ]
        window_blocks = [
            m.InspectorBlock(type="kv_list", title="窗口", items=window_items),
            m.InspectorBlock(type="kv_list", title="各进口", items=approach_items),
        ]
    else:
        window_blocks = [m.InspectorBlock(type="kv_list", title="窗口", items=[_kv("状态", "暂无窗口")])]
    tabs.append(m.InspectorTab(id="window", title="最近窗口", blocks=window_blocks))

    # 命令闭环：锚定执行体 + 可下发命令 + 最近命令/ack（按 object_id 关联）。
    anchor = state.registry.get(spec.agent_id)
    control_blocks = [
        m.InspectorBlock(
            type="kv_list",
            title="执行体",
            items=[
                _kv("目标 agent", spec.agent_id),
                _kv("可下发命令", anchor.command_types if anchor else []),
            ],
        ),
        m.InspectorBlock(
            type="event_list",
            title="最近命令/ack",
            items=_command_event_items(state.command_log.for_object(spec.id)),
        ),
    ]
    tabs.append(m.InspectorTab(id="control", title="命令闭环", blocks=control_blocks))

    return m.InspectorProjection(
        target=m.ProjectionTarget(kind="node", id=spec.id, title=spec.label), tabs=tabs
    )


def _agent_projection(record: AgentRecord, state, now: datetime) -> m.InspectorProjection:
    derived = record.derived_status(now)
    info_tab = m.InspectorTab(
        id="info",
        title="智能体",
        blocks=[
            m.InspectorBlock(
                type="kv_list",
                title="注册信息",
                items=[
                    _kv("类型", record.agent_type),
                    _kv("能力", record.capabilities),
                    _kv("可接收命令", record.command_types),
                    _kv("状态", derived.value),
                    _kv("注册于", iso_utc(record.registered_at)),
                ],
            )
        ],
    )
    hb_tab = m.InspectorTab(
        id="heartbeat",
        title="心跳",
        blocks=[
            m.InspectorBlock(
                type="kv_list",
                title="心跳",
                items=[
                    _kv("自报状态", record.reported_status or "（无）"),
                    _kv("最近心跳", iso_utc(record.last_heartbeat_ts) if record.last_heartbeat_ts else "（无）"),
                    _kv("派生状态", derived.value),
                    _kv("最近错误", record.last_error or "无"),
                ],
            )
        ],
    )
    cmd_tab = m.InspectorTab(
        id="control",
        title="命令闭环",
        blocks=[
            m.InspectorBlock(
                type="kv_list", title="可下发命令", items=[_kv(ct, "可用") for ct in record.command_types]
            ),
            m.InspectorBlock(
                type="event_list",
                title="最近命令/ack",
                items=_command_event_items(state.command_log.for_agent(record.agent_id)),
            ),
        ],
    )
    return m.InspectorProjection(
        target=m.ProjectionTarget(kind="node", id=record.agent_id, title=record.agent_id),
        tabs=[info_tab, hb_tab, cmd_tab],
    )


def _world_model_projection(state, now: datetime) -> m.InspectorProjection:
    snap = build_snapshot(state, now)
    overview = m.InspectorTab(
        id="overview",
        title="总览",
        blocks=[
            m.InspectorBlock(
                type="metric_grid",
                title="规模",
                items=[
                    _metric("路口", len(state.topology.intersections)),
                    _metric("智能体", snap.summary.agents),
                    _metric("关系", snap.summary.relations),
                    _metric("资源", snap.summary.resources),
                    _metric("平均健康", snap.summary.healthy_percent),
                ],
            ),
            m.InspectorBlock(
                type="kv_list",
                title="World Model",
                items=[
                    _kv("域", state.topology.region),
                    _kv("拓扑版本", state.topology.topology_version),
                    _kv("更新频率(Hz)", snap.summary.update_rate),
                ],
            ),
        ],
    )
    congestion = m.InspectorTab(
        id="congestion",
        title="拥堵概览",
        blocks=[
            m.InspectorBlock(
                type="event_list",
                title="路口状态",
                items=[
                    {
                        "title": f"{spec.label}",
                        "status": (s.congestion_level.value if (s := state.status_store.get(spec.id)) else "syncing"),
                        "time": (s.window.end if (s := state.status_store.get(spec.id)) else ""),
                        "severity": "info",
                    }
                    for spec in state.topology.intersections
                ],
            )
        ],
    )
    return m.InspectorProjection(
        target=m.ProjectionTarget(kind="world_model", id="traffic", title="交通管控 World Model"),
        tabs=[overview, congestion],
    )


def _edge_projection(state, edge_id: str) -> m.InspectorProjection | None:
    for road in state.topology.roads:
        if road.id == edge_id:
            return m.InspectorProjection(
                target=m.ProjectionTarget(kind="edge", id=edge_id, title=road.label),
                tabs=[
                    m.InspectorTab(
                        id="info",
                        title="路段",
                        blocks=[
                            m.InspectorBlock(
                                type="kv_list",
                                title="连接",
                                items=[
                                    _kv("起点", road.source),
                                    _kv("终点", road.target),
                                    _kv("类型", road.relation_type),
                                    _kv("有向", road.directed),
                                ],
                            )
                        ],
                    )
                ],
            )
    for src, dst, label, relation_type, directed in state.topology.agent_relations:
        if f"rel-{src}-{dst}" == edge_id:
            return m.InspectorProjection(
                target=m.ProjectionTarget(kind="edge", id=edge_id, title=label),
                tabs=[
                    m.InspectorTab(
                        id="info",
                        title="关系",
                        blocks=[
                            m.InspectorBlock(
                                type="kv_list",
                                title="连接",
                                items=[
                                    _kv("起点", src),
                                    _kv("终点", dst),
                                    _kv("类型", relation_type),
                                    _kv("有向", directed),
                                ],
                            )
                        ],
                    )
                ],
            )
    return None


def _resource_projection(state, resource_id: str) -> m.InspectorProjection | None:
    for spec in state.topology.resources:
        if spec.id == resource_id:
            return m.InspectorProjection(
                target=m.ProjectionTarget(kind="resource", id=resource_id, title=spec.label),
                tabs=[
                    m.InspectorTab(
                        id="info",
                        title="资源",
                        blocks=[
                            m.InspectorBlock(
                                type="kv_list",
                                title="资源",
                                items=[
                                    _kv("类型", spec.resource_type),
                                    _kv("方向", spec.direction),
                                    _kv("锚定", spec.anchor_agent_id),
                                ],
                            )
                        ],
                    )
                ],
            )
    return None


def _fallback_projection(kind: str, target_id: str) -> m.InspectorProjection:
    """未知目标也返回结构完整的 projection（target + tabs），避免前端整体回落 mock。"""

    safe_kind = kind if kind in ("world_model", "node", "edge", "resource") else "node"
    return m.InspectorProjection(
        target=m.ProjectionTarget(kind=safe_kind, id=target_id, title=target_id),  # type: ignore[arg-type]
        tabs=[
            m.InspectorTab(
                id="info",
                title="信息",
                blocks=[m.InspectorBlock(type="kv_list", title="未找到", items=[_kv("目标", "未在当前拓扑/registry 中找到")])],
            )
        ],
    )


def build_projection(state, kind: str, target_id: str, now: datetime | None = None) -> m.InspectorProjection:
    """按 (kind, id) 装配 InspectorProjection；始终返回结构完整的投影。"""

    now = now or state.now()
    if kind == "world_model":
        return _world_model_projection(state, now)
    if kind == "node":
        spec = state.topology.get_intersection(target_id)
        if spec is not None:
            return _intersection_projection(spec, state, now)
        record = state.registry.get(target_id)
        if record is not None:
            return _agent_projection(record, state, now)
        return _fallback_projection(kind, target_id)
    if kind == "edge":
        proj = _edge_projection(state, target_id)
        return proj or _fallback_projection(kind, target_id)
    if kind == "resource":
        proj = _resource_projection(state, target_id)
        return proj or _fallback_projection(kind, target_id)
    return _fallback_projection(kind, target_id)


# --------------------------------------------------------------------------- #
# 统一世界视图（/world）：跨域 agent + model + catalog（前端世界总览/model 视图用）
# --------------------------------------------------------------------------- #
#: 实体坐标 provider 钩子 —— 路口外的实体（摄像头等）坐标由此注册，**未来视频组提供**。
#: 形如 ``fn(entity_key) -> (x, y) | None``。
_LOCATION_PROVIDERS: list = []


def register_location_provider(fn) -> None:
    """注册一个 ``entity_key -> (x, y) | None`` 的坐标 provider（如摄像头坐标）。"""

    _LOCATION_PROVIDERS.append(fn)


def _resolve_entity_location(key: str, topology: Topology) -> tuple[float, float] | None:
    """实体 key → 坐标：先查拓扑路口，再问已注册 provider；都没有返回 None。"""

    spec = topology.get_intersection(key)
    if spec is not None:
        return (spec.x, spec.y)
    for fn in _LOCATION_PROVIDERS:
        try:
            loc = fn(key)
        except Exception:  # noqa: BLE001 - provider 失败不应拖垮 /world
            loc = None
        if loc is not None:
            return (float(loc[0]), float(loc[1]))
    return None


def _agent_world_location(record: AgentRecord, topology: Topology) -> dict | None:
    """agent 地图位置 = 其通道（先 produces 后 consumes）首个可解析 key 的实体坐标；无则非地理。"""

    for channels in (record.produces, record.consumes):
        for ch in channels:
            for key in ch.keys:
                loc = _resolve_entity_location(key, topology)
                if loc is not None:
                    return {"x": loc[0], "y": loc[1], "entity": key}
    return None


def _channel_dicts(channels: list) -> list[dict]:
    return [{"topic": ch.topic, "keys": list(ch.keys)} for ch in channels]


def _topic_domain(topic: str) -> str:
    """Return the ANP domain segment from ``anp.<domain>...`` topics."""

    parts = topic.split(".")
    if len(parts) < 3 or parts[0] != "anp":
        return ""
    return parts[1]


def _produced_topics(record: AgentRecord) -> set[str]:
    return {ch.topic for ch in record.produces}


def _consumed_topics(record: AgentRecord) -> set[str]:
    return {ch.topic for ch in record.consumes}


def _record_topics(record: AgentRecord) -> set[str]:
    return _produced_topics(record) | _consumed_topics(record)


def _record_keys(record: AgentRecord) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ch in [*record.produces, *record.consumes]:
        for key in ch.keys:
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _add_member(out: list[str], record: AgentRecord) -> None:
    if record.agent_id not in out:
        out.append(record.agent_id)


def _derive_model_members(records: list[AgentRecord]) -> dict[str, list[str]]:
    """Infer model membership from registered channel contracts.

    Explicit ``members`` declared by a model remain valid bootstrap hints, but a
    model should not need to know every adapter id in advance. The derived
    membership therefore uses the model's topic boundary as the primary signal:

    - an agent producing a topic consumed by the model is a member;
    - an agent consuming a topic produced by the model is a member;
    - agents in the same ANP domain that share an entity key with those direct
      members join the same model. This covers paired perception/exec agents:
      SV perception enters through observation, SV exec enters through the same
      intersection key on the command topic.
    """

    by_id = {rec.agent_id: rec for rec in records}
    leaf_records = [rec for rec in records if rec.agent_type != "model"]
    model_records = [rec for rec in records if rec.agent_type == "model"]
    derived: dict[str, list[str]] = {}

    for model in model_records:
        members: list[str] = []

        for member_id in model.members:
            rec = by_id.get(member_id)
            if rec is not None and rec.agent_type != "model":
                _add_member(members, rec)

        model_consumes = _consumed_topics(model)
        model_produces = _produced_topics(model)
        model_topics = model_consumes | model_produces
        model_domains = {domain for domain in (_topic_domain(t) for t in model_topics) if domain}

        for rec in leaf_records:
            if _produced_topics(rec) & model_consumes or _consumed_topics(rec) & model_produces:
                _add_member(members, rec)

        member_keys: set[str] = set()
        for member_id in members:
            rec = by_id.get(member_id)
            if rec is not None:
                member_keys.update(_record_keys(rec))

        if member_keys:
            for rec in leaf_records:
                if rec.agent_id in members:
                    continue
                rec_keys = set(_record_keys(rec))
                if not rec_keys or rec_keys.isdisjoint(member_keys):
                    continue
                rec_domains = {domain for domain in (_topic_domain(t) for t in _record_topics(rec)) if domain}
                if model_domains and rec_domains and rec_domains.isdisjoint(model_domains):
                    continue
                _add_member(members, rec)

        derived[model.agent_id] = members

    return derived


def build_world(state, now: datetime | None = None) -> dict:
    """统一世界只读视图：跨域 agent（含位置/通道/归属 model）+ model（含成员）+ catalog。

    纯读 registry + 静态拓扑（坐标），不碰 Kafka。供 ``GET /api/agent-network/world``。
    """

    now = now or state.now()
    registry: Registry = state.registry
    topology: Topology = state.topology
    records = registry.all()
    model_members = _derive_model_members(records)
    # 已下线的 model 不该再出现在 agent 的「被 model 使用」里（与下面 models 列表过滤一致，
    # 避免注销后的残留 model 仍挂在成员 agent 的 governed_by 上）。
    offline_model_ids = {
        rec.agent_id for rec in records
        if rec.agent_type == "model" and _AGENT_STATUS_MAP[rec.derived_status(now)] == "offline"
    }

    agents = [
        {
            "id": rec.agent_id,
            "agent_type": rec.agent_type,
            "status": _AGENT_STATUS_MAP[rec.derived_status(now)],
            "capabilities": list(rec.capabilities),
            "command_types": list(rec.command_types),
            "weight": rec.weight,
            "produces": _channel_dicts(rec.produces),
            "consumes": _channel_dicts(rec.consumes),
            "location": _agent_world_location(rec, topology),  # None = 非地理公民
            "governed_by": [mid for mid, members in model_members.items() if rec.agent_id in members and mid not in offline_model_ids],
        }
        for rec in records
    ]

    # 过滤下线残留 model：deregister / 心跳过期后不再显示，避免旧 model（如已停的 spec）
    # 残留在世界总览里造成「重复 model」的错觉。
    models = []
    for rec in registry.models():
        status = _AGENT_STATUS_MAP[rec.derived_status(now)]
        if status == "offline":
            continue
        models.append({
            "model_id": rec.agent_id,
            "status": status,
            "members": list(model_members.get(rec.agent_id, rec.members)),
            "produce_topics": [ch.topic for ch in rec.produces],
            "subscribe_topics": [ch.topic for ch in rec.consumes],
            "weight": rec.weight,
        })

    return {
        "ok": True,
        "generated_at": iso_utc(now),
        "agents": agents,
        "models": models,
        "catalog": registry.catalog_by_topic(),
    }
