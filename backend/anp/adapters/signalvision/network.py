"""SV 路网几何 → 紧凑可绘制几何的纯映射（前端经网关 `/sv-network` 取真实路网）。

SV `GET /api/network` 返回 `network_data{edge,node,inter,...}`（LibSignal 网络格式，~78KB），
`GET /api/junctions/summary` 返回各路口 `position`/`congestion_level`。本模块把两者归并成一份
**紧凑、前端可直接画**的几何：边为线段（incnode_coord→outnode_coord）、路口为带拥堵的点。

纯函数、无 IO、可单测（与 `mapping.py` 同样只懂 SV 原生结构，不在网关里散搓）。
"""

from __future__ import annotations

from typing import Any


def _coord(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def build_road_geometry(network_body: dict | None, summary_body: dict | None) -> dict:
    """把 SV `/api/network` + `/api/junctions/summary` 归并成紧凑几何。

    返回 ``{junctions:[{id,x,y,congestion,junction_type,is_active,total_vehicles,total_halting}],
    edges:[{id,x1,y1,x2,y2,lanes,length}], bounds:{minX,maxX,minY,maxY}, junction_count}``。
    """

    nd = (network_body or {}).get("network_data") or {}
    edges_raw = nd.get("edge") if isinstance(nd.get("edge"), dict) else {}
    xs: list[float] = []
    ys: list[float] = []
    edges: list[dict] = []
    for edge_id, e in edges_raw.items():
        if not isinstance(e, dict):
            continue
        inc = _coord(e.get("incnode_coord"))
        out = _coord(e.get("outnode_coord"))
        if inc is None or out is None:
            continue
        edges.append(
            {
                "id": str(edge_id),
                "x1": inc[0], "y1": inc[1],
                "x2": out[0], "y2": out[1],
                "lanes": int(e.get("nlanes") or len(e.get("lanes") or []) or 1),
                "length": float(e.get("length") or 0.0),
            }
        )
        xs += [inc[0], out[0]]
        ys += [inc[1], out[1]]

    summaries = (summary_body or {}).get("summaries") if isinstance(summary_body, dict) else None
    junctions: list[dict] = []
    for s in summaries or []:
        if not isinstance(s, dict):
            continue
        pos = _coord(s.get("position"))
        if pos is None:
            continue
        junctions.append(
            {
                "id": str(s.get("junction_id")),
                "x": pos[0], "y": pos[1],
                "congestion": float(s.get("congestion_level") or 0.0),
                "junction_type": str(s.get("junction_type") or ""),
                "is_active": bool(s.get("is_active", True)),
                "total_vehicles": int(s.get("total_vehicles") or 0),
                "total_halting": int(s.get("total_halting") or 0),
            }
        )
        xs.append(pos[0])
        ys.append(pos[1])

    bounds = (
        {"minX": min(xs), "maxX": max(xs), "minY": min(ys), "maxY": max(ys)}
        if xs and ys
        else {"minX": 0.0, "maxX": 1.0, "minY": 0.0, "maxY": 1.0}
    )
    return {
        "junctions": junctions,
        "edges": edges,
        "bounds": bounds,
        "junction_count": int((network_body or {}).get("junction_count") or len(junctions)),
    }
