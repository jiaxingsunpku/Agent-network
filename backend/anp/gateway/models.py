"""网关 HTTP 响应模型 —— 字段刻意对齐老前端契约（snake_case）。

前端 normalizer 同时接受 snake/camel（agentNetworkClient.ts），这里统一用 snake_case
输出。结构对应 types.ts 的 NetworkSnapshot / AgentNode / AgentEdge / PhysicalResource，
以及 agentNetworkClient.ts 的 InspectorProjection / 命令响应（docs/gateway-api.md §1/§2/§3）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeStatus = Literal["online", "warning", "offline", "syncing"]


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


# --------------------------------------------------------------------------- #
# snapshot（docs/gateway-api.md §1）
# --------------------------------------------------------------------------- #
class Summary(BaseModel):
    agents: int
    relations: int
    resources: int
    healthy_percent: int
    kafka_lag_ms: int
    update_rate: float


class Node(BaseModel):
    id: str
    label: str
    node_type: Literal["agent", "region", "service"]
    group: str
    position: Position
    status: NodeStatus
    health: int
    tags: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    id: str
    source: str
    target: str
    label: str
    directed: bool
    relation_type: str
    status: NodeStatus
    metrics: dict[str, Any] = Field(default_factory=dict)


class Resource(BaseModel):
    id: str
    label: str
    resource_type: Literal["camera", "database", "detector", "simulator", "storage", "controller"]
    anchor_agent_id: str
    height: float
    direction: Literal["input", "output", "bidirectional"]
    status: NodeStatus
    metrics: dict[str, Any] = Field(default_factory=dict)


class TrendPoint(BaseModel):
    t: float
    value: float


class Event(BaseModel):
    id: str
    severity: Literal["info", "warning", "critical"]
    title: str
    target_id: str
    time: str


class NetworkSnapshot(BaseModel):
    version: str = "gateway"
    generated_at: str
    topology_version: str
    region: str
    summary: Summary
    nodes: list[Node]
    edges: list[Edge]
    resources: list[Resource]
    trend: list[TrendPoint] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# projection（docs/gateway-api.md §2）
# --------------------------------------------------------------------------- #
class InspectorBlock(BaseModel):
    # 允许 items/value/data 任意结构；前端按 type 渲染。
    model_config = ConfigDict(extra="allow")

    type: Literal["metric_grid", "kv_list", "event_list", "timeseries", "json"]
    title: str | None = None
    items: list[Any] | None = None
    value: Any | None = None
    data: Any | None = None


class InspectorTab(BaseModel):
    id: str
    title: str
    blocks: list[InspectorBlock]


class ProjectionTarget(BaseModel):
    kind: Literal["world_model", "node", "edge", "resource"]
    id: str
    title: str


class InspectorProjection(BaseModel):
    target: ProjectionTarget
    tabs: list[InspectorTab]


# --------------------------------------------------------------------------- #
# 命令（docs/gateway-api.md §3）
# --------------------------------------------------------------------------- #
class CommandRequest(BaseModel):
    """前端命令请求体。``broadcast`` / ``agent_ids`` 一律拒绝（extra=forbid 兜底 +
    显式校验给出明确错误码）。"""

    model_config = ConfigDict(extra="forbid")

    target_agent_id: str | None = None
    command_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    site_id: str | None = None
    region_id: str | None = None
    object_id: str | None = None
    expires_in_sec: float | None = None


class CommandTarget(BaseModel):
    agent_id: str | None = None
    region_id: str | None = None


class CommandResponse(BaseModel):
    ok: Literal[True] = True
    command_id: str
    topic: str
    target: CommandTarget
    status: str = "published"
    message_id: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorBody
