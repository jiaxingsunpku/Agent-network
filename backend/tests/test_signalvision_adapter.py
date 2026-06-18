"""SignalVision 感知 adapter 回归（纯逻辑 + 客户端 + 一轮编排，均无 Kafka）。

覆盖 docs/adapters.md 的映射契约：lane→方向归并、累计通过量差分分摊、契约校验、
SV 不可达降级。运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd=backend/）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from anp.adapters.signalvision import (
    SignalVisionAdapter,
    SignalVisionAdapterConfig,
    SignalVisionClient,
    heartbeat_envelope,
    lifecycle_envelope,
    map_junction_to_observation,
    resolve_direction,
    throughput_delta,
)
from anp.adapters.signalvision.config import (
    DIRECTION_STRATEGY_AUTO,
    DIRECTION_STRATEGY_ROUND_ROBIN,
)
from anp.adapters.signalvision.mapping import _distribute_int
from anp.contracts import Direction, Envelope, EventType, ObservationPayload, parse_payload

_SAMPLE_PATH = Path(__file__).parent / "data" / "signalvision_junction_sample.json"


def _sample() -> dict:
    return json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# resolve_direction
# --------------------------------------------------------------------------- #
def test_resolve_direction_token():
    assert resolve_direction("edge_north_1_1_0", 0, DIRECTION_STRATEGY_AUTO) is Direction.NORTH
    assert resolve_direction("edge_south_1_1_0", 1, DIRECTION_STRATEGY_AUTO) is Direction.SOUTH
    assert resolve_direction("edge_east_1_1_0", 2, DIRECTION_STRATEGY_AUTO) is Direction.EAST
    assert resolve_direction("edge_west_1_1_0", 3, DIRECTION_STRATEGY_AUTO) is Direction.WEST


def test_resolve_direction_chinese_and_single_letter():
    assert resolve_direction("北进口_0", 0, DIRECTION_STRATEGY_AUTO) is Direction.NORTH
    assert resolve_direction("lane_w", 3, DIRECTION_STRATEGY_AUTO) is Direction.WEST


def test_resolve_direction_round_robin_and_fallback():
    # round_robin 一律按序号，无视 lane_id。
    assert resolve_direction("edge_north_x", 1, DIRECTION_STRATEGY_ROUND_ROBIN) is Direction.SOUTH
    # auto 抽不到 token → 回退轮询。
    assert resolve_direction("lane_12345", 2, DIRECTION_STRATEGY_AUTO) is Direction.EAST


# --------------------------------------------------------------------------- #
# throughput_delta / _distribute_int
# --------------------------------------------------------------------------- #
def test_throughput_delta():
    assert throughput_delta(1200, None) == 0          # 首轮无基线
    assert throughput_delta(1215, 1200) == 15         # 正常增量
    assert throughput_delta(7, 1200) == 7             # 计数器回退（重启）→ 取当前


def test_distribute_int_sum_and_even():
    assert _distribute_int(0, [3, 1, 0]) == [0, 0, 0]
    assert sum(_distribute_int(15, [8, 6, 3, 4])) == 15
    assert _distribute_int(8, [0, 0, 0, 0]) == [2, 2, 2, 2]   # 全 0 权重 → 均分
    # 比例：权重最大者分得最多。
    d = _distribute_int(10, [7, 3])
    assert d == [7, 3] and sum(d) == 10


# --------------------------------------------------------------------------- #
# map_junction_to_observation
# --------------------------------------------------------------------------- #
def test_map_junction_first_poll_zero_throughput():
    payload, passed = map_junction_to_observation(
        _sample(), "gg-xiongchu-minzu", prev_passed=None, strategy=DIRECTION_STRATEGY_AUTO
    )
    assert passed == 1200
    assert isinstance(payload, ObservationPayload)
    assert payload.intersection_id == "gg-xiongchu-minzu"
    # 四个方向都在（lane_id 带 token），次序固定 N/S/E/W。
    assert [a.direction for a in payload.approaches] == [
        Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST
    ]
    # 首轮无基线 → 通过量全 0。
    assert all(a.vehicle_count == 0 for a in payload.approaches)
    # 滞留直接取自车道。
    by_dir = {a.direction: a for a in payload.approaches}
    assert by_dir[Direction.NORTH].halting_count == 5
    assert by_dir[Direction.SOUTH].halting_count == 3
    # 速度直接取（单车道即该车道速度）。
    assert by_dir[Direction.NORTH].mean_speed_mps == pytest.approx(3.2)
    # 延误留空，交系统级推导。
    assert all(a.mean_delay_sec is None for a in payload.approaches)


def test_map_junction_second_poll_distributes_delta():
    sample = _sample()
    # 第二轮：累计通过量 +20。
    sample["metrics"]["total_vehicles_passed"] = 1220
    payload, passed = map_junction_to_observation(
        sample, "gg-xiongchu-minzu", prev_passed=1200, strategy=DIRECTION_STRATEGY_AUTO
    )
    assert passed == 1220
    total_flow = sum(a.vehicle_count for a in payload.approaches)
    assert total_flow == 20  # 差分 20 全部分摊掉
    # 瞬时车数 N=8 最多 → 分得最多。
    by_dir = {a.direction: a.vehicle_count for a in payload.approaches}
    assert by_dir[Direction.NORTH] >= by_dir[Direction.EAST]


def test_map_junction_no_incoming_returns_none():
    payload, passed = map_junction_to_observation(
        {"incoming_lanes": {}, "metrics": {"total_vehicles_passed": 5}},
        "x",
        prev_passed=None,
        strategy=DIRECTION_STRATEGY_AUTO,
    )
    assert payload is None and passed == 5


def test_map_junction_round_robin_groups_lanes_without_tokens():
    sample = _sample()
    # 改成无 token 的 lane_id，round_robin 仍能归并出方向。
    sample["incoming_lanes"] = {
        f"lane_{i}": v for i, v in enumerate(sample["incoming_lanes"].values())
    }
    payload, _ = map_junction_to_observation(
        sample, "gg-xiongchu-minzu", prev_passed=None, strategy=DIRECTION_STRATEGY_ROUND_ROBIN
    )
    assert len(payload.approaches) == 4
    assert {a.direction for a in payload.approaches} == set(Direction)


def test_mapped_observation_passes_contract():
    payload, _ = map_junction_to_observation(
        _sample(), "gg-xiongchu-minzu", prev_passed=1000, strategy=DIRECTION_STRATEGY_AUTO
    )
    # 再过一遍 pydantic 校验（extra=forbid / 非负约束）。
    ObservationPayload.model_validate(payload.model_dump())


# --------------------------------------------------------------------------- #
# SignalVisionAdapter.map_detail —— 映射 + 状态（通过量基线）演进
# --------------------------------------------------------------------------- #
def test_adapter_map_detail_unmapped_junction():
    adapter = SignalVisionAdapter(SignalVisionAdapterConfig(junction_map={"j1": "gg-xiongchu-minzu"}))
    assert adapter.map_detail("other", _sample()) is None


def test_adapter_map_detail_state_evolves():
    cfg = SignalVisionAdapterConfig(junction_map={"intersection_1_1": "gg-xiongchu-minzu"})
    adapter = SignalVisionAdapter(cfg)

    first = adapter.map_detail("intersection_1_1", _sample())
    assert first is not None
    intersection_id, payload1 = first
    assert intersection_id == "gg-xiongchu-minzu"
    assert sum(a.vehicle_count for a in payload1.approaches) == 0  # 首轮

    sample2 = _sample()
    sample2["metrics"]["total_vehicles_passed"] = 1230
    _, payload2 = adapter.map_detail("intersection_1_1", sample2)
    assert sum(a.vehicle_count for a in payload2.approaches) == 30  # 1230-1200


# --------------------------------------------------------------------------- #
# 一轮编排：fake client + fake producer（无 Kafka）
# --------------------------------------------------------------------------- #
class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

        class _F:  # 占位 future
            pass

        return _F()

    def flush(self):
        pass


class _FakeStatus:
    def __init__(self, ok: bool, body=None):
        self.ok = ok
        self.status_code = 200 if ok else None
        self.body = body or {}


class _FakeClient:
    def __init__(self, *, ok: bool, junction: dict | None):
        self._ok = ok
        self._junction = junction

    def get_status(self):
        return _FakeStatus(self._ok, {"running": True} if self._ok else {"message": "down"})

    def junction_state(self, junction_id):
        return self._junction if self._ok else None


def _envs(producer: _FakeProducer) -> list[Envelope]:
    return [Envelope.model_validate(v) for (_t, _k, v) in producer.sent]


def test_poll_once_publishes_heartbeat_and_observation():
    cfg = SignalVisionAdapterConfig(junction_map={"intersection_1_1": "gg-xiongchu-minzu"})
    adapter = SignalVisionAdapter(cfg, client=_FakeClient(ok=True, junction=_sample()))
    producer = _FakeProducer()

    res = adapter.poll_once(producer)
    assert res.reachable and res.published == 1 and res.intersections == ["gg-xiongchu-minzu"]

    envs = _envs(producer)
    hb = [e for e in envs if e.event_type == EventType.AGENT_HEARTBEAT]
    obs = [e for e in envs if e.event_type == EventType.OBSERVATION_TRAFFIC_INTERSECTION]
    assert len(hb) == 1 and parse_payload(hb[0]).status == "online"
    assert len(obs) == 1
    ob_payload = parse_payload(obs[0])
    assert isinstance(ob_payload, ObservationPayload)
    assert obs[0].scope.object_id == "gg-xiongchu-minzu"
    assert obs[0].quality.confidence == pytest.approx(cfg.confidence)
    assert obs[0].source.agent_id == cfg.agent_id


def test_poll_once_degraded_when_sv_unreachable():
    adapter = SignalVisionAdapter(
        SignalVisionAdapterConfig(junction_map={"intersection_1_1": "gg-xiongchu-minzu"}),
        client=_FakeClient(ok=False, junction=None),
    )
    producer = _FakeProducer()
    res = adapter.poll_once(producer)
    assert not res.reachable and res.published == 0 and res.skipped == 1

    envs = _envs(producer)
    hb = [e for e in envs if e.event_type == EventType.AGENT_HEARTBEAT]
    assert len(hb) == 1
    hb_payload = parse_payload(hb[0])
    assert hb_payload.status == "degraded" and hb_payload.last_error
    # 不可达不发观测。
    assert not [e for e in envs if e.event_type == EventType.OBSERVATION_TRAFFIC_INTERSECTION]


# --------------------------------------------------------------------------- #
# lifecycle / heartbeat envelope
# --------------------------------------------------------------------------- #
def test_lifecycle_and_heartbeat_envelopes():
    reg = lifecycle_envelope(agent_id="traffic-perception-sv-001", registered=True)
    assert reg.event_type == EventType.AGENT_REGISTERED
    payload = parse_payload(reg)
    assert payload.agent_type == "signalvision"
    assert payload.capabilities == ["perception"]
    assert payload.command_types == []  # 纯感知，不接命令

    dereg = lifecycle_envelope(agent_id="traffic-perception-sv-001", registered=False)
    assert dereg.event_type == EventType.AGENT_DEREGISTERED

    hb = heartbeat_envelope(agent_id="traffic-perception-sv-001", status="online")
    assert parse_payload(hb).status == "online"


# --------------------------------------------------------------------------- #
# SignalVisionClient —— 进程内真实 HTTP server
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    junction = None  # 类变量，测试装填

    def log_message(self, *args):  # 静音
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/simulation/status":
            self._send(200, {"running": True, "mode": "inference"})
        elif self.path.startswith("/api/junctions/"):
            jid = self.path.rsplit("/", 1)[-1]
            if self.junction and jid == self.junction["junction_id"]:
                self._send(200, {"junction": self.junction, "success": True})
            else:
                self._send(200, {"success": False, "message": f"路口 {jid} 不存在"})
        else:
            self._send(404, {"message": "not found"})


@pytest.fixture()
def sv_server():
    _Handler.junction = _sample()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_client_status_and_junction_state(sv_server):
    client = SignalVisionClient(sv_server, timeout_sec=2.0)
    assert client.get_status().ok
    junction = client.junction_state("intersection_1_1")
    assert junction is not None and junction["junction_id"] == "intersection_1_1"
    # 不存在的路口 → None。
    assert client.junction_state("no_such") is None


def test_client_unreachable_returns_none():
    # 未监听端口 → 不可达。
    client = SignalVisionClient("http://127.0.0.1:1", timeout_sec=0.5)
    assert not client.get_status().ok
    assert client.junction_state("intersection_1_1") is None
