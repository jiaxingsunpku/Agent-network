#!/usr/bin/env python3
"""端到端命令闭环冒烟（P6 信号控制）：网关/命令源 → Kafka command → SV 执行体 →
调（桩）SV /update → Kafka ack。

真实 SV Dashboard 本机未跑（启动要拉 SUMO，独立重项目），故用**进程内桩 HTTP server**
接收 `POST /api/junctions/<id>/update` 并记录 traffic_light；其余链路全真实经过 Kafka
两个 topic（command + ack）。覆盖 docs/protocol.md §5 全分支：

- 合法命令 → completed（含「网关真实发布路径」build_command_envelope + 授权），并断言桩 SV
  收到映射后的 traffic_light（phase_state / next_switch_time）；
- 越界参数 → rejected（本地 Safety Guard）；
- object_id 未映射 SV junction → rejected（路由约束）；
- 过期命令 → expired；
- 重复 command_id → duplicate；
- 非本体目标 → 忽略（无 ack）。

用 assign + seek_to_end 建基线，仅消费本次新增，可重复运行。退出码 0 = 通过。

前置：Kafka 已起、topic 已建（deploy/README.md）。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/smoke_signalvision_exec.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kafka import TopicPartition  # noqa: E402

from anp.adapters.signalvision import (  # noqa: E402
    SV_EXEC_AGENT_ID,
    SignalVisionExecConfig,
    SignalVisionExecutor,
    exec_lifecycle_envelope,
)
from anp.contracts import (  # noqa: E402
    AckPayload,
    CommandPayload,
    CommandType,
    Envelope,
    Source,
    SourceSystem,
    command_envelope,
    expires_at_iso,
    parse_payload,
)
from anp.gateway import GatewayState, PublishUnavailable  # noqa: E402
from anp.gateway.commands import build_command_envelope  # noqa: E402
from anp.gateway.config import GATEWAY_AGENT_ID, GatewayConfig  # noqa: E402
from anp.messaging import make_consumer, make_producer, publish  # noqa: E402
from anp.registry import seed_default_registry  # noqa: E402

BOOTSTRAP = os.environ.get("ANP_BOOTSTRAP", "localhost:9092")
SV_JUNCTION = "intersection_1_1"
INTERSECTION = "gg-xiongchu-minzu"
OK_PARAMS = {"desired_phase": "north_south_green", "duration_s": 25}


# --------------------------------------------------------------------------- #
# 桩 SV Dashboard：接收 POST /api/junctions/<id>/update，记录 traffic_light。
# --------------------------------------------------------------------------- #
class _StubSV(BaseHTTPRequestHandler):
    received: list[tuple[str | None, dict | None]] = []
    sim_received: list[tuple[str, str | None]] = []  # (action, config)

    def log_message(self, *args):
        pass

    def _send(self, body: dict, code: int = 200):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/simulation/status":
            self._send({"running": True, "mode": "inference"})
        else:
            self._send({"success": False, "message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        data = json.loads(raw) if raw else {}
        # 仿真启停（control_signal_inference）。
        if self.path == "/api/simulation/start":
            _StubSV.sim_received.append(("start", data.get("config")))
            self._send({"success": True, "message": "仿真启动成功（集成模式）", "pid": 4321})
            return
        if self.path == "/api/simulation/stop":
            _StubSV.sim_received.append(("stop", None))
            self._send({"success": True, "message": "仿真已停止"})
            return
        parts = self.path.strip("/").split("/")  # api / junctions / <id> / update
        junction_id = parts[2] if len(parts) >= 4 and parts[3] == "update" else None
        if junction_id is None:
            self._send({"success": False, "message": "bad path"}, code=404)
            return
        _StubSV.received.append((junction_id, data.get("traffic_light")))
        self._send({"success": True, "message": f"路口 {junction_id} 数据已更新"})


def _start_stub() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _StubSV)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _assign_from_end(topic: str, suffix: str):
    consumer = make_consumer(
        [], group_id=f"anp-svexec-smoke-{suffix}", bootstrap_servers=BOOTSTRAP, consumer_timeout_ms=8000
    )
    parts = consumer.partitions_for_topic(topic)
    if not parts:
        raise SystemExit(f"[svexec-smoke] FAIL: topic 无分区: {topic}")
    tps = [TopicPartition(topic, p) for p in parts]
    consumer.assign(tps)
    consumer.seek_to_end()
    for tp in tps:
        consumer.position(tp)
    return consumer


def _gateway_source() -> Source:
    return Source(system=SourceSystem.PLATFORM, agent_id=GATEWAY_AGENT_ID)


def _manual_command(
    command_id: str,
    *,
    target: str,
    params: dict,
    object_id: str | None = INTERSECTION,
    expires_sec: float = 30.0,
    command_type: CommandType = CommandType.SET_SIGNAL_PLAN,
) -> Envelope:
    return command_envelope(
        source=_gateway_source(),
        target_agent_id=target,
        payload=CommandPayload(command_id=command_id, command_type=command_type, params=params),
        expires_at=expires_at_iso(expires_sec),
        object_id=object_id,
    )


def main() -> int:
    print(f"[svexec-smoke] bootstrap={BOOTSTRAP} target={SV_EXEC_AGENT_ID} {INTERSECTION}->{SV_JUNCTION}")
    server, sv_url = _start_stub()
    try:
        cmd_consumer = _assign_from_end("anp.traffic.command.v1", "in")
        ack_consumer = _assign_from_end("anp.traffic.ack.v1", "out")

        producer = make_producer(bootstrap_servers=BOOTSTRAP)
        # registry：seed + 把 SV 执行体经 lifecycle 注册进白名单（网关授权路径需要）。
        registry = seed_default_registry()
        registry.apply_envelope(exec_lifecycle_envelope(agent_id=SV_EXEC_AGENT_ID, registered=True))
        state = GatewayState(config=GatewayConfig(with_consumers=False), registry=registry, producer=producer)

        # 1) 网关真实发布路径（合法命令 + 授权）→ 期望 completed + 桩 SV 收到 traffic_light。
        env_gw, cid_gw = build_command_envelope(
            {
                "target_agent_id": SV_EXEC_AGENT_ID,
                "command_type": "set_signal_plan",
                "payload": OK_PARAMS,
                "object_id": INTERSECTION,
            },
            registry,
        )
        try:
            state.publish_command(env_gw)
        except PublishUnavailable:
            raise SystemExit("[svexec-smoke] FAIL: 网关 producer 不可用")

        # 2..6) 手工命令（精确控制 command_id 以测各分支）。
        cid_valid = "svx-valid-001"
        publish(producer, "anp.traffic.command.v1", _manual_command(cid_valid, target=SV_EXEC_AGENT_ID, params=OK_PARAMS))
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command("svx-reject-001", target=SV_EXEC_AGENT_ID, params={"desired_phase": "north_south_green", "duration_s": 999}),
        )
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command("svx-route-001", target=SV_EXEC_AGENT_ID, params=OK_PARAMS, object_id="not-mapped"),
        )
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command("svx-expired-001", target=SV_EXEC_AGENT_ID, params=OK_PARAMS, expires_sec=-5.0),
        )
        publish(producer, "anp.traffic.command.v1", _manual_command(cid_valid, target=SV_EXEC_AGENT_ID, params=OK_PARAMS))  # 重复
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command("svx-other-001", target="some-other-agent", params=OK_PARAMS),
        )
        # 8..9) control_signal_inference（粗粒度、真驱动 SUMO）：合法 start + 越界 action。
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command(
                "svx-infer-001", target=SV_EXEC_AGENT_ID,
                params={"action": "start", "algorithm": "maxpressure"},
                command_type=CommandType.CONTROL_SIGNAL_INFERENCE,
            ),
        )
        publish(
            producer, "anp.traffic.command.v1",
            _manual_command(
                "svx-infer-bad-001", target=SV_EXEC_AGENT_ID,
                params={"action": "pause"},
                command_type=CommandType.CONTROL_SIGNAL_INFERENCE,
            ),
        )
        producer.flush()
        print("[svexec-smoke] 已发布 9 条命令（网关路径 1 + 手工 6 + 控制推理 2）")

        # 执行体 drain：用真实 client 指向桩 SV。
        executor = SignalVisionExecutor(
            SignalVisionExecConfig(sv_base_url=sv_url, junction_map={SV_JUNCTION: INTERSECTION})
        )
        ack_producer = make_producer(bootstrap_servers=BOOTSTRAP)
        executor.run(cmd_consumer, ack_producer)
        cmd_consumer.close()
        ack_producer.close()
        print(
            f"[svexec-smoke] executor processed={executor.processed} rejected={executor.rejected} "
            f"failed={executor.failed} ignored={executor.ignored} invalid={executor.dropped_invalid}"
        )

        # 读 ack 归集。
        acks: dict[str, list[str]] = {}
        while True:
            batch = ack_consumer.poll(timeout_ms=4000, max_records=64)
            if not batch:
                break
            for records in batch.values():
                for rec in records:
                    env = Envelope.model_validate(rec.value)
                    payload = parse_payload(env)
                    assert isinstance(payload, AckPayload)
                    acks.setdefault(payload.command_id, []).append(payload.status.value)
        ack_consumer.close()

        failures: list[str] = []

        def _expect(cid: str, want: str, note: str = "") -> None:
            got = acks.get(cid, [])
            if want in got:
                print(f"[svexec-smoke]   ok: {cid} -> {want}{note}")
            else:
                failures.append(f"{cid}: 期望含 {want}，实际 {got}")

        _expect(cid_gw, "completed", "（网关真实发布路径）")
        _expect(cid_valid, "completed")
        _expect("svx-reject-001", "rejected", "（越界 duration 被 Safety Guard 拒绝）")
        _expect("svx-route-001", "rejected", "（object_id 未映射 SV junction）")
        _expect("svx-expired-001", "expired")
        _expect(cid_valid, "duplicate", "（重复命令）")
        _expect("svx-infer-001", "completed", "（control_signal_inference start maxpressure → 真驱动 SUMO）")
        _expect("svx-infer-bad-001", "rejected", "（control_signal_inference 越界 action 被 Safety Guard 拒绝）")

        if "svx-other-001" in acks:
            failures.append("异目标命令不应产生 ack")
        else:
            print("[svexec-smoke]   ok: 异目标命令被忽略（无 ack）")

        # 桩 SV 应收到 1 次 start(maxpressure)（控制推理合法命令），越界 action 不触达。
        if _StubSV.sim_received == [("start", "maxpressure")]:
            print(f"[svexec-smoke]   ok: 桩 SV 收到仿真控制 {_StubSV.sim_received}（control_signal_inference 真驱动路径）")
        else:
            failures.append(f"桩 SV 仿真控制调用不符，期望 [('start','maxpressure')]，实际 {_StubSV.sim_received}")

        # 桩 SV 应至少收到 2 次合法 update（网关路径 + cid_valid），均落在 SV_JUNCTION 且映射正确。
        sv_updates = [tl for jid, tl in _StubSV.received if jid == SV_JUNCTION]
        if len(sv_updates) < 2:
            failures.append(f"桩 SV 期望 ≥2 次 {SV_JUNCTION} update，实际 {len(_StubSV.received)} 次: {_StubSV.received}")
        else:
            tl = sv_updates[0]
            ok_map = (
                isinstance(tl, dict)
                and tl.get("phase_state") == "north_south_green"
                and tl.get("next_switch_time") == 25.0
                and tl.get("phase_duration") == 0.0
            )
            if not ok_map:
                failures.append(f"桩 SV 收到的 traffic_light 映射不符: {tl}")
            else:
                print(f"[svexec-smoke]   ok: 桩 SV 收到 {len(sv_updates)} 次合法 update，traffic_light={tl}")
        # 被拒命令不应触达 SV。
        if any(jid != SV_JUNCTION for jid, _ in _StubSV.received):
            failures.append(f"桩 SV 收到非目标 junction 的写入: {_StubSV.received}")

        if failures:
            for f in failures:
                print(f"[svexec-smoke] FAIL: {f}")
            return 1

        print(
            "[svexec-smoke] PASS: SV 命令执行闭环全分支（set_signal_plan completed/rejected×2/expired/duplicate/ignored "
            "+ control_signal_inference start completed/越界 rejected）+ SV 写端点 + 仿真控制端点映射一致。"
        )
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
