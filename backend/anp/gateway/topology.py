"""静态路网拓扑 —— 路口节点位置、路段边、物理资源（docs/gateway-api.md §1.2/§1.3）。

网关把「动态 World Status + registry」叠加到这份「静态拓扑」上拼出 snapshot：
- 路口节点的 id/label/position 来自这里；metrics/status 来自 World Status。
- 路段边、智能体↔实体关系边来自这里。
- 物理资源（检测器=input、信号控制器/状态库=output）来自这里。

v1 用 雄楚大道 走廊三个路口的小拓扑；扩展只改本文件，不动映射逻辑。
真实路网/真实路段长属 P5 adapter，不在本期。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import GATEWAY_AGENT_ID


@dataclass(frozen=True)
class IntersectionSpec:
    """一个路口的静态信息（动态指标由 World Status 叠加）。"""

    id: str
    label: str
    x: float
    y: float
    #: 锚定到该路口的执行/感知智能体（命令面板与关系边用）。
    agent_id: str = "traffic-virtual-001"


@dataclass(frozen=True)
class RoadEdgeSpec:
    """两个路口之间的路段连接（无向当作双向，directed=False）。"""

    id: str
    source: str
    target: str
    label: str
    directed: bool = False
    relation_type: str = "road"


@dataclass(frozen=True)
class ResourceSpec:
    """物理资源（检测器/控制器/状态库）。"""

    id: str
    label: str
    resource_type: str  # camera|database|detector|simulator|storage|controller
    direction: str      # input|output|bidirectional
    anchor_agent_id: str
    height: float = 0.0


@dataclass(frozen=True)
class Topology:
    """整张静态拓扑。"""

    topology_version: str
    region: str
    intersections: tuple[IntersectionSpec, ...]
    roads: tuple[RoadEdgeSpec, ...]
    resources: tuple[ResourceSpec, ...]
    #: 智能体↔路口的关系边（感知/控制/聚合），(source, target, label, relation_type, directed)。
    agent_relations: tuple[tuple[str, str, str, str, bool], ...] = field(default_factory=tuple)

    def intersection_ids(self) -> list[str]:
        return [it.id for it in self.intersections]

    def get_intersection(self, intersection_id: str) -> IntersectionSpec | None:
        return next((it for it in self.intersections if it.id == intersection_id), None)


# --------------------------------------------------------------------------- #
# v1 交通域默认拓扑（雄楚大道走廊）
# --------------------------------------------------------------------------- #
_INTERSECTIONS = (
    IntersectionSpec(id="gg-xiongchu-minzu", label="雄楚-民族", x=-220.0, y=0.0),
    IntersectionSpec(id="gg-xiongchu-luxiang", label="雄楚-鲁巷", x=0.0, y=0.0),
    IntersectionSpec(id="gg-xiongchu-guanggu", label="雄楚-光谷", x=220.0, y=0.0),
)

_ROADS = (
    RoadEdgeSpec(
        id="road-minzu-luxiang",
        source="gg-xiongchu-minzu",
        target="gg-xiongchu-luxiang",
        label="雄楚大道（民族→鲁巷）",
    ),
    RoadEdgeSpec(
        id="road-luxiang-guanggu",
        source="gg-xiongchu-luxiang",
        target="gg-xiongchu-guanggu",
        label="雄楚大道（鲁巷→光谷）",
    ),
)

#: 虚拟体感知+控制三个路口；系统级智能体从虚拟体上行观测做聚合。
_AGENT_RELATIONS = (
    ("traffic-virtual-001", "gg-xiongchu-minzu", "感知/控制", "controls", True),
    ("traffic-virtual-001", "gg-xiongchu-luxiang", "感知/控制", "controls", True),
    ("traffic-virtual-001", "gg-xiongchu-guanggu", "感知/控制", "controls", True),
    ("traffic-system-001", "traffic-virtual-001", "窗口聚合", "aggregates", True),
)

_RESOURCES = (
    ResourceSpec(
        id="detector-virtual-001",
        label="路口检测器（虚拟）",
        resource_type="detector",
        direction="input",
        anchor_agent_id="traffic-virtual-001",
        height=18.0,
    ),
    ResourceSpec(
        id="controller-signal-001",
        label="信号配时控制器",
        resource_type="controller",
        direction="output",
        anchor_agent_id="traffic-virtual-001",
        height=22.0,
    ),
    ResourceSpec(
        id="status-store-001",
        label="World Status 状态层",
        resource_type="storage",
        direction="output",
        anchor_agent_id="traffic-system-001",
        height=26.0,
    ),
    ResourceSpec(
        id="gateway-readmodel-001",
        label="网关读模型",
        resource_type="database",
        direction="bidirectional",
        anchor_agent_id=GATEWAY_AGENT_ID,
        height=24.0,
    ),
)

DEFAULT_TOPOLOGY = Topology(
    topology_version="traffic-v1",
    region="traffic",
    intersections=_INTERSECTIONS,
    roads=_ROADS,
    resources=_RESOURCES,
    agent_relations=_AGENT_RELATIONS,
)
