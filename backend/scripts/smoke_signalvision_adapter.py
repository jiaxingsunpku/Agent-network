#!/usr/bin/env python3
"""端到端冒烟：桩 SV Dashboard → SignalVision adapter → Kafka → 系统级 → World Status。

真实 SV Dashboard 本机未跑（启动要拉 SUMO，独立重项目），故用**进程内桩 HTTP server**
回放一个真实形态的 SV junction（每次取详情把累计通过量 +DELTA，驱动通过量差分），
其余链路全真实经过 Kafka 两个 topic（观测 + 状态）。退出码 0 = 通过。

前置：Kafka 已起、topic 已建（deploy/README.md）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_signalvision_adapter.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kafka import TopicPartition  # noqa: E402

from anp.adapters.signalvision import SignalVisionAdapter, SignalVisionAdapterConfig  # noqa: E402
from anp.contracts import (  # noqa: E402
    Envelope,
    EventType,
    IntersectionStatusPayload,
    ObservationPayload,
    TrafficTopics,
    iso_utc,
    parse_payload,
)
from anp.messaging import make_consumer, make_producer  # noqa: E402
from anp.system_agent import SystemAgent  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
SV_JUNCTION = "intersection_1_1"
INTERSECTION = "gg-xiongchu-minzu"
N_POLLS = 12             # 每 2s 一拍 → 覆盖 [0,10)/[10,20)/[20,30) 三个窗口
INTERVAL_SEC = 2
EXPECTED_WINDOWS = 3
PASSED_DELTA = 2         # 每拍累计通过量增量（2 veh/2s ≈ 3600 veh/h 量级）
_SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "tests" / "data" / "signalvision_junction_sample.json").read_text("utf-8"))


# --------------------------------------------------------------------------- #
# 桩 SV Dashboard：每次取 junction 详情把 total_vehicles_passed 递增。
# --------------------------------------------------------------------------- #
class _StubHandler(BaseHTTPRequestHandler):
    _passed = _SAMPLE["metrics"]["total_vehicles_passed"]

    def log_message(self, *args):
        pass

    def _send(self, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/simulation/status":
            self._send({"running": True, "mode": "inference"})
        elif self.path.startswith("/api/junctions/"):
            junction = json.loads(json.dumps(_SAMPLE))  # 深拷贝
            junction["metrics"]["total_vehicles_passed"] = _StubHandler._passed
            _StubHandler._passed += PASSED_DELTA
            self._send({"junction": junction, "success": True})
        else:
            self._send({"success": False, "message": "not found"})


def _start_stub() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _assign_from_end(topic: str, suffix: str):
    consumer = make_consumer([], group_id=f"anp-svsmoke-{suffix}", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000)
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[sv-smoke] FAIL: topic 无分区（未建？）: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def main() -> int:
    print(f"[sv-smoke] bootstrap={BOOTSTRAP} sv_junction={SV_JUNCTION} -> {INTERSECTION} polls={N_POLLS}")
    server, sv_url = _start_stub()
    try:
        obs_consumer = _assign_from_end(TrafficTopics.OBSERVATION, "obs")
        status_consumer = _assign_from_end(TrafficTopics.STATUS_INTERSECTION, "status")

        # 1) adapter 按受控 event_ts 轮询桩 SV，发布按方向观测。
        producer = make_producer(bootstrap_servers=BOOTSTRAP)
        adapter = SignalVisionAdapter(
            SignalVisionAdapterConfig(sv_base_url=sv_url, junction_map={SV_JUNCTION: INTERSECTION}),
            # 默认 HTTP 客户端即可（桩 server 真实 HTTP）。
        )
        base_epoch = (int(datetime.now(timezone.utc).timestamp()) // 10) * 10
        base = datetime.fromtimestamp(base_epoch, tz=timezone.utc)
        published = 0
        for i in range(N_POLLS):
            ts = iso_utc(base + timedelta(seconds=i * INTERVAL_SEC))
            res = adapter.poll_once(producer, event_ts=ts)
            published += res.published
        producer.flush()
        print(f"[sv-smoke] adapter published={published} observations（含通过量差分分摊）")
        if published != N_POLLS:
            print(f"[sv-smoke] FAIL: 期望发布 {N_POLLS} 条观测，实际 {published}")
            return 1

        # 2) 系统级智能体消费观测、产出 World Status。
        sys_producer = make_producer(bootstrap_servers=BOOTSTRAP)
        sys_agent = SystemAgent(producer=sys_producer)
        consumed = 0
        first_obs_checked = False
        while consumed < N_POLLS:
            batch = obs_consumer.poll(timeout_ms=4000, max_records=64)
            if not batch:
                break
            for records in batch.values():
                for rec in records:
                    env = Envelope.model_validate(rec.value)
                    if env.event_type != EventType.OBSERVATION_TRAFFIC_INTERSECTION:
                        continue
                    if env.scope.object_id != INTERSECTION:
                        continue
                    if not first_obs_checked:
                        ob = parse_payload(env)
                        assert isinstance(ob, ObservationPayload)
                        print(
                            f"[sv-smoke]   首条观测 agent={env.source.agent_id} "
                            f"approaches={[a.direction.value for a in ob.approaches]} "
                            f"halting={[a.halting_count for a in ob.approaches]}"
                        )
                        first_obs_checked = True
                    sys_agent.feed_envelope(env)
                    consumed += 1
        sys_agent.flush()
        sys_producer.flush()
        obs_consumer.close()
        print(
            f"[sv-smoke] system-agent consumed={consumed} windows_emitted={sys_agent.windows_emitted} "
            f"dropped_late={sys_agent.aggregator.dropped_late}"
        )
        if sys_agent.windows_emitted != EXPECTED_WINDOWS:
            print(f"[sv-smoke] FAIL: 期望结算 {EXPECTED_WINDOWS} 个窗口，实际 {sys_agent.windows_emitted}")
            return 1

        # 3) 从状态层取回 World Status 并校验。
        fetched: list[IntersectionStatusPayload] = []
        while len(fetched) < EXPECTED_WINDOWS:
            batch = status_consumer.poll(timeout_ms=4000, max_records=64)
            if not batch:
                break
            for records in batch.values():
                for rec in records:
                    env = Envelope.model_validate(rec.value)
                    if env.scope.object_id != INTERSECTION:
                        continue
                    payload = parse_payload(env)
                    assert isinstance(payload, IntersectionStatusPayload)
                    fetched.append(payload)
        status_consumer.close()
        if len(fetched) < EXPECTED_WINDOWS:
            print(f"[sv-smoke] FAIL: 状态层只取回 {len(fetched)}/{EXPECTED_WINDOWS} 条 World Status")
            return 1

        for st in fetched:
            ok = (
                st.intersection_id == INTERSECTION
                and st.window.size_sec == 10
                and st.window.sample_count > 0
                and len(st.approaches) >= 1
                and 0.0 <= st.congestion_index <= 1.0
            )
            if not ok:
                print(f"[sv-smoke] FAIL: World Status 字段非法: {st.model_dump()}")
                return 1
            print(
                f"[sv-smoke]   window[{st.window.start}..{st.window.end}] samples={st.window.sample_count} "
                f"queue={st.queue_length_m:.1f}m flow={st.flow_veh_h:.0f}veh/h "
                f"speed={st.mean_speed_kmh:.1f}km/h delay={st.mean_delay_sec:.1f}s -> {st.congestion_level.value}"
            )

        latest = sys_agent.store.get(INTERSECTION)
        if latest is None or latest.window.end != fetched[-1].window.end:
            print("[sv-smoke] FAIL: 内存当前态与最后一个窗口不一致")
            return 1

        print(f"[sv-smoke] PASS: 桩 SV → adapter → Kafka → 系统级 World Status 链路一致（结算 {len(fetched)} 窗口）")
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
