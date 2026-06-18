#!/usr/bin/env python3
"""网关冒烟（TestClient，无需 Kafka）—— 校验五接口契约与命令校验分支。

注入一条 World Status 与一个 FakeProducer，用 starlette TestClient 打：
- snapshot：结构完整、节点含路口+智能体、application/json；
- projection：路口/智能体/world_model 均返回 target+tabs；
- commands：成功（FakeProducer 收到命令）+ 400（缺 target / broadcast / 非法类型）+ 403（白名单外）；
- timeseries / edge-inference：结构化失败（ok:false）。

退出码 0 = 通过。验证「前端 mock 回落判定」的关键正例：snapshot/projection 始终
返回合法结构与 JSON content-type。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_gateway.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient  # noqa: E402

from anp.contracts import (  # noqa: E402
    ApproachStatus,
    CongestionLevel,
    Direction,
    IntersectionStatusPayload,
    StatusWindow,
    iso_utc,
)
from anp.gateway import GatewayState, create_app  # noqa: E402
from anp.gateway.config import GatewayConfig  # noqa: E402
from anp.registry import seed_default_registry  # noqa: E402
from anp.system_agent import LatestStatusStore  # noqa: E402


class _FakeFuture:
    def get(self, timeout=None):
        return {"partition": 0, "offset": 0}


class FakeProducer:
    """记录 send 的假 producer：不连 Kafka，命令发布走成功路径。"""

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
        return _FakeFuture()

    def flush(self, *a, **k):
        pass


def _seed_status(store: LatestStatusStore) -> None:
    now = datetime.now(timezone.utc)
    store.update(
        IntersectionStatusPayload(
            intersection_id="gg-xiongchu-minzu",
            window=StatusWindow(
                start=iso_utc(now - timedelta(seconds=10)), end=iso_utc(now), size_sec=10, sample_count=5
            ),
            queue_length_m=35.0,
            flow_veh_h=180.0,
            mean_speed_kmh=29.9,
            mean_delay_sec=41.2,
            congestion_level=CongestionLevel.CONGESTED,
            congestion_index=0.62,
            approaches=[
                ApproachStatus(direction=Direction.NORTH, queue_length_m=14.0, flow_veh_h=72.0, mean_speed_kmh=26.8)
            ],
        )
    )


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"[smoke-gateway] FAIL: {msg}")
    print(f"[smoke-gateway]   ok: {msg}")


def main() -> int:
    store = LatestStatusStore()
    _seed_status(store)
    producer = FakeProducer()
    state = GatewayState(
        config=GatewayConfig(with_consumers=False),
        status_store=store,
        registry=seed_default_registry(),
        producer=producer,
    )
    client = TestClient(create_app(state))

    # 1) snapshot。
    r = client.get("/api/agent-network/snapshot")
    _check(r.status_code == 200, "snapshot 200")
    _check("application/json" in r.headers.get("content-type", ""), "snapshot content-type json")
    snap = r.json()
    _check(snap["version"] == "gateway", "snapshot version=gateway")
    node_types = {n["node_type"] for n in snap["nodes"]}
    _check("region" in node_types and "agent" in node_types, "snapshot 同时含路口与智能体节点")
    minzu = next((n for n in snap["nodes"] if n["id"] == "gg-xiongchu-minzu"), None)
    _check(minzu is not None and minzu["metrics"]["state"] == "拥堵", "路口节点 metrics.state 来自 World Status")
    _check(minzu["status"] == "warning", "拥堵→warning 状态映射")
    _check(len(snap["edges"]) >= 2 and len(snap["resources"]) >= 1, "snapshot 含边与资源")

    # 2) projection：路口 / 智能体 / world_model。
    r = client.get("/api/agent-network/projection", params={"kind": "node", "id": "gg-xiongchu-minzu"})
    proj = r.json()
    _check(r.status_code == 200 and proj.get("target") and isinstance(proj.get("tabs"), list), "路口 projection 结构完整")
    tab_ids = {t["id"] for t in proj["tabs"]}
    _check({"status", "window", "control"} <= tab_ids, "路口 projection 含 status/window/control 三 tab")

    r = client.get("/api/agent-network/projection", params={"kind": "node", "id": "traffic-virtual-001"})
    proj = r.json()
    _check(proj.get("target", {}).get("id") == "traffic-virtual-001", "智能体 projection target 正确")

    r = client.get("/api/agent-network/projection", params={"kind": "world_model", "id": "traffic"})
    _check(isinstance(r.json().get("tabs"), list), "world_model projection 含 tabs")

    # 3) commands：成功。
    r = client.post(
        "/api/agent-network/commands",
        json={
            "target_agent_id": "traffic-virtual-001",
            "command_type": "set_signal_plan",
            "payload": {"desired_phase": "north_south_green", "duration_s": 25},
            "object_id": "gg-xiongchu-minzu",
        },
    )
    body = r.json()
    _check(r.status_code == 200 and body["ok"] and body["status"] == "published", "命令发布成功 200")
    _check(len(producer.sent) == 1 and producer.sent[0][0] == "anp.traffic.command.v1", "命令落到 command topic")

    # 4) commands：错误分支。
    r = client.post("/api/agent-network/commands", json={"command_type": "set_signal_plan"})
    _check(r.status_code == 400 and r.json()["error"]["code"] == "missing_target_agent_id", "缺 target → 400")

    r = client.post(
        "/api/agent-network/commands",
        json={"target_agent_id": "x", "command_type": "set_signal_plan", "broadcast": True},
    )
    _check(r.status_code == 400 and r.json()["error"]["code"] == "broadcast_not_allowed", "broadcast → 400")

    r = client.post(
        "/api/agent-network/commands", json={"target_agent_id": "traffic-virtual-001", "command_type": "nope"}
    )
    _check(r.status_code == 400 and r.json()["error"]["code"] == "invalid_command_type", "非法命令类型 → 400")

    r = client.post(
        "/api/agent-network/commands", json={"target_agent_id": "ghost-001", "command_type": "set_signal_plan"}
    )
    _check(r.status_code == 403 and r.json()["error"]["code"] == "target_not_whitelisted", "白名单外目标 → 403")

    r = client.post(
        "/api/agent-network/commands", json={"target_agent_id": "traffic-system-001", "command_type": "set_signal_plan"}
    )
    _check(
        r.status_code == 403 and r.json()["error"]["code"] == "command_not_allowed_for_target",
        "目标不接收该命令类型 → 403",
    )

    # 5) timeseries / edge-inference 结构化失败。
    r = client.get("/api/agent-network/timeseries/health")
    _check(r.json().get("error", {}).get("code") == "timeseries_disabled", "timeseries 结构化未启用")
    r = client.post("/api/agent-network/edge-inference", json={"agent_id": "traffic-virtual-001"})
    _check(r.json().get("error", {}).get("code") == "unsupported", "edge-inference 结构化 unsupported")

    print("[smoke-gateway] PASS: 网关五接口契约与命令校验分支全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
