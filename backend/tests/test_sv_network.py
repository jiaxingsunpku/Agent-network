"""SV 路网几何纯映射回归（`build_road_geometry`）——前端经网关 `/sv-network` 取真实路网。"""

from __future__ import annotations

from anp.adapters.signalvision.network import build_road_geometry

NETWORK = {
    "junction_count": 9,
    "network_data": {
        "edge": {
            "e1": {"incnode_coord": [10.0, 20.0], "outnode_coord": [30.0, 40.0], "nlanes": 2, "length": 100.5},
            "e2": {"incnode_coord": [30.0, 40.0], "outnode_coord": [50.0, 10.0], "lanes": ["e2_0"], "length": 60.0},
            "bad": {"incnode_coord": [1.0], "outnode_coord": [2.0, 3.0]},  # 残缺坐标 → 跳过
        }
    },
}
SUMMARY = {
    "summaries": [
        {"junction_id": "1", "position": [30.0, 40.0], "congestion_level": 0.7, "junction_type": "traffic_light", "is_active": False, "total_vehicles": 5, "total_halting": 2},
        {"junction_id": "2", "position": None},  # 无坐标 → 跳过
    ]
}


def test_build_geometry_maps_edges_and_junctions():
    geo = build_road_geometry(NETWORK, SUMMARY)
    assert len(geo["edges"]) == 2  # bad 被跳过
    e1 = next(e for e in geo["edges"] if e["id"] == "e1")
    assert (e1["x1"], e1["y1"], e1["x2"], e1["y2"]) == (10.0, 20.0, 30.0, 40.0)
    assert e1["lanes"] == 2 and e1["length"] == 100.5
    e2 = next(e for e in geo["edges"] if e["id"] == "e2")
    assert e2["lanes"] == 1  # 无 nlanes → 用 lanes 列表长度
    assert len(geo["junctions"]) == 1  # junction 2 无坐标被跳过
    j = geo["junctions"][0]
    assert j["id"] == "1" and j["congestion"] == 0.7 and j["is_active"] is False and j["total_vehicles"] == 5
    assert geo["junction_count"] == 9


def test_build_geometry_bounds_cover_all_coords():
    geo = build_road_geometry(NETWORK, SUMMARY)
    b = geo["bounds"]
    assert b["minX"] == 10.0 and b["maxX"] == 50.0
    assert b["minY"] == 10.0 and b["maxY"] == 40.0


def test_build_geometry_empty_safe():
    geo = build_road_geometry({}, {})
    assert geo["edges"] == [] and geo["junctions"] == []
    assert geo["bounds"] == {"minX": 0.0, "maxX": 1.0, "minY": 0.0, "maxY": 1.0}

    geo2 = build_road_geometry(None, None)
    assert geo2["edges"] == [] and geo2["junctions"] == []
