"""网关回归：snapshot / projection 结构与映射、命令校验分支、鉴权、冷路径占位。

用 starlette TestClient + 注入的 GatewayState（FakeProducer，无 Kafka）。映射纯逻辑，
断言契约字段（snake_case）与状态映射（docs/gateway-api.md §1/§2/§3）。
运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from anp.contracts import (
    ApproachStatus,
    CongestionLevel,
    Direction,
    IntersectionStatusPayload,
    StatusWindow,
    iso_utc,
)
from anp.contracts import Channel, TrafficTopics
from anp.gateway import GatewayState, build_snapshot, create_app
from anp.gateway.config import GatewayConfig
from anp.registry import Registry, seed_default_registry
from anp.system_agent import LatestStatusStore


class _FakeFuture:
    def get(self, timeout=None):
        return {"ok": True}


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
        return _FakeFuture()

    def flush(self, *a, **k):
        pass


def _status(intersection: str, *, level: CongestionLevel, age_sec: float = 0.0, ci: float = 0.5):
    now = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    return IntersectionStatusPayload(
        intersection_id=intersection,
        window=StatusWindow(start=iso_utc(now - timedelta(seconds=10)), end=iso_utc(now), size_sec=10, sample_count=5),
        queue_length_m=35.0,
        flow_veh_h=180.0,
        mean_speed_kmh=29.9,
        mean_delay_sec=41.2,
        congestion_level=level,
        congestion_index=ci,
        approaches=[ApproachStatus(direction=Direction.NORTH, queue_length_m=14.0, flow_veh_h=72.0, mean_speed_kmh=26.8)],
    )


def _make_state(*, producer=None, config: GatewayConfig | None = None) -> GatewayState:
    store = LatestStatusStore()
    store.update(_status("gg-xiongchu-minzu", level=CongestionLevel.CONGESTED, ci=0.62))
    return GatewayState(
        config=config or GatewayConfig(with_consumers=False),
        status_store=store,
        registry=seed_default_registry(),
        producer=producer if producer is not None else FakeProducer(),
    )


def _client(state: GatewayState) -> TestClient:
    return TestClient(create_app(state))


def test_world_endpoint_agents_models_catalog():
    reg = Registry()
    reg.register(
        agent_id="leaf-1",
        agent_type="virtual",
        capabilities=["perception"],
        produces=[Channel(topic=TrafficTopics.OBSERVATION, keys=["gg-xiongchu-minzu"])],
        consumes=[Channel(topic=TrafficTopics.COMMAND, keys=["gg-xiongchu-minzu"])],
    )
    reg.register(
        agent_id="m-traffic",
        agent_type="model",
        capabilities=["model"],
        members=["leaf-1"],
        produces=[Channel(topic=TrafficTopics.STATUS_INTERSECTION)],
        consumes=[Channel(topic=TrafficTopics.OBSERVATION)],
    )
    state = GatewayState(config=GatewayConfig(with_consumers=False), registry=reg, producer=FakeProducer())
    resp = _client(state).get("/api/agent-network/world")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"]

    agents = {a["id"]: a for a in body["agents"]}
    # 位置由通道 key→拓扑路口坐标派生。
    assert agents["leaf-1"]["location"]["entity"] == "gg-xiongchu-minzu"
    assert agents["leaf-1"]["location"]["x"] == -220.0
    # 反向索引：leaf 归属 model。
    assert agents["leaf-1"]["governed_by"] == ["m-traffic"]
    # model 本身也是 agent 节点（万物皆 agent）。
    assert agents["m-traffic"]["agent_type"] == "model"

    models = {mm["model_id"]: mm for mm in body["models"]}
    assert models["m-traffic"]["members"] == ["leaf-1"]
    assert TrafficTopics.STATUS_INTERSECTION in models["m-traffic"]["produce_topics"]
    # catalog 按 topic 反查谁产。
    assert "leaf-1" in body["catalog"][TrafficTopics.OBSERVATION]["producers"]


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #
def test_snapshot_structure_and_mapping():
    client = _client(_make_state())
    r = client.get("/api/agent-network/snapshot")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]
    snap = r.json()
    assert snap["version"] == "gateway"
    assert snap["topology_version"] == "traffic-v1"
    # 节点同时含路口（region）与智能体（agent）。
    by_id = {n["id"]: n for n in snap["nodes"]}
    assert by_id["gg-xiongchu-minzu"]["node_type"] == "region"
    assert by_id["traffic-virtual-001"]["node_type"] == "agent"
    # 路口指标映射（world-status.md §5）。
    metrics = by_id["gg-xiongchu-minzu"]["metrics"]
    assert metrics["flow"] == 180 and metrics["state"] == "拥堵"
    # 拥堵 → warning。
    assert by_id["gg-xiongchu-minzu"]["status"] == "warning"
    # summary 自洽。
    assert snap["summary"]["relations"] == len(snap["edges"])
    assert snap["summary"]["resources"] == len(snap["resources"])
    assert snap["summary"]["agents"] >= 2


def test_snapshot_stale_status_offline():
    store = LatestStatusStore()
    store.update(_status("gg-xiongchu-minzu", level=CongestionLevel.SMOOTH, age_sec=120.0))
    state = GatewayState(config=GatewayConfig(with_consumers=False), status_store=store, registry=seed_default_registry())
    snap = build_snapshot(state)
    node = next(n for n in snap.nodes if n.id == "gg-xiongchu-minzu")
    assert node.status == "offline"  # 超时未更新


def test_snapshot_unknown_intersection_syncing():
    # 没有任何 World Status 的路口仍出现在节点里，状态 syncing（拓扑兜底，节点非空）。
    state = GatewayState(config=GatewayConfig(with_consumers=False), registry=seed_default_registry())
    snap = build_snapshot(state)
    node = next(n for n in snap.nodes if n.id == "gg-xiongchu-guanggu")
    assert node.status == "syncing"


# --------------------------------------------------------------------------- #
# projection
# --------------------------------------------------------------------------- #
def test_projection_intersection_tabs():
    client = _client(_make_state())
    r = client.get("/api/agent-network/projection", params={"kind": "node", "id": "gg-xiongchu-minzu"})
    proj = r.json()
    assert proj["target"]["id"] == "gg-xiongchu-minzu"
    assert isinstance(proj["tabs"], list)
    assert {t["id"] for t in proj["tabs"]} >= {"status", "window", "control"}


def test_projection_agent_and_world_model():
    client = _client(_make_state())
    r = client.get("/api/agent-network/projection", params={"kind": "node", "id": "traffic-virtual-001"})
    assert r.json()["target"]["id"] == "traffic-virtual-001"
    r = client.get("/api/agent-network/projection", params={"kind": "world_model", "id": "traffic"})
    assert isinstance(r.json()["tabs"], list)


def test_projection_unknown_still_valid():
    # 未知目标也返回 target + tabs（避免前端整体回落 mock）。
    client = _client(_make_state())
    r = client.get("/api/agent-network/projection", params={"kind": "node", "id": "does-not-exist"})
    proj = r.json()
    assert proj["target"] and isinstance(proj["tabs"], list)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def _cmd_body(**over):
    body = {
        "target_agent_id": "traffic-virtual-001",
        "command_type": "set_signal_plan",
        "payload": {"desired_phase": "north_south_green", "duration_s": 25},
        "object_id": "gg-xiongchu-minzu",
    }
    body.update(over)
    return body


def test_command_success_publishes():
    producer = FakeProducer()
    client = _client(_make_state(producer=producer))
    r = client.post("/api/agent-network/commands", json=_cmd_body())
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["status"] == "published"
    assert body["topic"] == "anp.traffic.command.v1"
    assert len(producer.sent) == 1 and producer.sent[0][0] == "anp.traffic.command.v1"


def test_command_missing_target():
    client = _client(_make_state())
    r = client.post("/api/agent-network/commands", json={"command_type": "set_signal_plan"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_target_agent_id"


def test_command_broadcast_rejected():
    client = _client(_make_state())
    r = client.post("/api/agent-network/commands", json=_cmd_body(broadcast=True))
    assert r.status_code == 400 and r.json()["error"]["code"] == "broadcast_not_allowed"
    r = client.post("/api/agent-network/commands", json=_cmd_body(agent_ids=["a", "b"]))
    assert r.status_code == 400 and r.json()["error"]["code"] == "broadcast_not_allowed"


def test_command_invalid_type():
    client = _client(_make_state())
    r = client.post("/api/agent-network/commands", json=_cmd_body(command_type="reboot_everything"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_command_type"


def test_command_whitelist_403():
    client = _client(_make_state())
    r = client.post("/api/agent-network/commands", json=_cmd_body(target_agent_id="ghost-001"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "target_not_whitelisted"
    r = client.post("/api/agent-network/commands", json=_cmd_body(target_agent_id="traffic-system-001"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "command_not_allowed_for_target"


def test_command_kafka_unavailable_503():
    # producer=None → 503。
    state = GatewayState(config=GatewayConfig(with_consumers=False), registry=seed_default_registry(), producer=None)
    client = _client(state)
    r = client.post("/api/agent-network/commands", json=_cmd_body())
    assert r.status_code == 503 and r.json()["error"]["code"] == "kafka_unavailable"


def test_command_invalid_expires():
    client = _client(_make_state())
    r = client.post("/api/agent-network/commands", json=_cmd_body(expires_in_sec=99999))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_expires_in_sec"


# --------------------------------------------------------------------------- #
# 冷路径 / edge-inference 占位
# --------------------------------------------------------------------------- #
def test_timeseries_disabled():
    client = _client(_make_state())
    r = client.get("/api/agent-network/timeseries/health")
    body = r.json()
    assert body["ok"] is False and body["error"]["code"] == "timeseries_disabled"


def test_edge_inference_unsupported():
    client = _client(_make_state())
    r = client.post("/api/agent-network/edge-inference", json={"agent_id": "traffic-virtual-001"})
    body = r.json()
    assert body["ok"] is False and body["error"]["code"] == "unsupported"


# --------------------------------------------------------------------------- #
# 鉴权（默认关 / 开启后）
# --------------------------------------------------------------------------- #
def test_auth_required_when_enabled():
    cfg = GatewayConfig(with_consumers=False, require_auth=True, read_token="r-tok", admin_token="a-tok")
    client = _client(_make_state(config=cfg))
    # 无 token → 401。
    assert client.get("/api/agent-network/snapshot").status_code == 401
    # read token → 读 200。
    assert client.get("/api/agent-network/snapshot", headers={"Authorization": "Bearer r-tok"}).status_code == 200
    # 命令用 read token → 401（需 admin）。
    r = client.post("/api/agent-network/commands", json=_cmd_body(), headers={"Authorization": "Bearer r-tok"})
    assert r.status_code == 401
    # 命令用 admin token → 200。
    r = client.post("/api/agent-network/commands", json=_cmd_body(), headers={"Authorization": "Bearer a-tok"})
    assert r.status_code == 200 and r.json()["ok"]


def test_healthz():
    client = _client(_make_state())
    assert client.get("/healthz").json()["ok"] is True
