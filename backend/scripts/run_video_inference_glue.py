#!/usr/bin/env python3
"""ANP<->vision hub 视频推理胶水 sidecar（P8 step2，部署到 wangxuan 宿主机运行）。

不属于 ANP 后端进程，不用 anp 包/conda；运行在 vision hub 真身机器（wangxuan
/nvme2/VLM/agents_for_vision_hub/scripts/）的宿主机 Python 3.8 + aiokafka 0.11 + httpx。
仓库内此副本仅作版本管理/评审/复原来源。

闭环（step2「收命令->dispatch->产结果」胶水，非侵入旁路）:

  visionhub.world_model.info.v1 (info_type=video_inference_request)   <- ANP 命令桥译出
    -> [本 sidecar] 消费 -> 调本机 vision hub HTTP POST /api/v1/world-model/demo-dispatch
       （真实多智能体推理，复用其运行中容器）-> 轮询 final_answer
    -> 产 edge.observation.result.v1（observation.traffic.video_text，
       trace.correlation_id=command_id，payload 带 camera/road）
  edge.observation.result.v1 -> ANP 结果桥 -> anp.video.perception.text.v1 -> P7 入库 -> 问答

要点：关联键=command_id（三处一致）；绕开 canonical_observation_adapter（其 correlation_id=run_id
无法关联命令）；dispatch 失败/超时也产「异常」结果，永远闭环；独立 consumer group，非侵入。
跨机 Kafka 经反向 SSH 隧道把本机 localhost:9092 透到 ANP broker。

用法（wangxuan）: python3 scripts/run_video_inference_glue.py [--from-beginning] [--duration 600]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

logger = logging.getLogger("vh-glue")

INFO_TYPE = "video_inference_request"
RESULT_EVENT_TYPE = "observation.traffic.video_text"
VISIONHUB_AGENT_ID = "video-visionhub-001"

_CATEGORY_RULES = [
    (("事故", "碰撞", "追尾", "刮擦"), "事故"),
    (("拥堵", "堵", "排队", "缓行"), "拥堵"),
    (("违章", "闯红灯", "压线", "逆行", "违停"), "违章"),
    (("施工", "占道", "围挡"), "施工"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _derive_category(*texts: str) -> Optional[str]:
    blob = " ".join(t for t in texts if t)
    for keys, cat in _CATEGORY_RULES:
        if any(k in blob for k in keys):
            return cat
    return None


def _http_json_sync(method: str, url: str, body: Optional[dict], timeout: float) -> dict:
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _http_json(method: str, url: str, body: Optional[dict] = None, timeout: float = 60.0) -> dict:
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=body)
            resp.raise_for_status()
            return resp.json()
    return await asyncio.get_event_loop().run_in_executor(
        None, _http_json_sync, method, url, body, timeout
    )


class VideoInferenceGlue:
    def __init__(self, args: argparse.Namespace) -> None:
        self.bootstrap = args.bootstrap
        self.info_topic = args.info_topic
        self.result_topic = args.result_topic
        self.api_base = args.api_base.rstrip("/")
        self.group = args.group
        self.from_beginning = args.from_beginning
        self.poll_timeout = args.poll_timeout
        self.poll_interval = args.poll_interval
        self._stop = asyncio.Event()
        self._producer = None
        self._consumer = None
        self.handled = 0
        self.skipped = 0
        self.emitted = 0
        self.errors = 0

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap, client_id="vh-glue-producer"
        )
        self._consumer = AIOKafkaConsumer(
            self.info_topic,
            bootstrap_servers=self.bootstrap,
            group_id=self.group,
            client_id="vh-glue-consumer",
            enable_auto_commit=True,
            auto_offset_reset="earliest" if self.from_beginning else "latest",
        )
        await self._producer.start()
        await self._consumer.start()
        logger.info(
            "started: info=%s result=%s api=%s group=%s bootstrap=%s",
            self.info_topic, self.result_topic, self.api_base, self.group, self.bootstrap,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            await self._consumer.stop()
        if self._producer is not None:
            await self._producer.stop()

    async def run_forever(self, duration: Optional[float]) -> None:
        loop = asyncio.get_event_loop()
        deadline = None if duration is None else loop.time() + duration
        while not self._stop.is_set():
            if deadline is not None and loop.time() >= deadline:
                break
            try:
                batches = await self._consumer.getmany(timeout_ms=1000, max_records=10)
            except Exception as exc:  # noqa: BLE001
                logger.exception("consume failed: %s", exc)
                await asyncio.sleep(1.0)
                continue
            for _, records in batches.items():
                for msg in records:
                    await self._handle(msg)

    async def _handle(self, msg: Any) -> None:
        try:
            raw = msg.value.decode("utf-8") if isinstance(msg.value, (bytes, bytearray)) else str(msg.value)
            env = json.loads(raw)
        except Exception:  # noqa: BLE001
            self.skipped += 1
            return
        if not isinstance(env, dict):
            self.skipped += 1
            return
        info = env.get("payload") if isinstance(env.get("payload"), dict) else {}
        if info.get("info_type") != INFO_TYPE:
            self.skipped += 1
            return

        trace = env.get("trace") if isinstance(env.get("trace"), dict) else {}
        command_id = trace.get("correlation_id") or info.get("command_id") or _new_id()
        prompt = str(info.get("prompt") or "请对该路段视频做一次分析，描述是否有事故、拥堵或违章。")
        camera_id = info.get("camera_id")
        road_name = info.get("road_name")
        intersection_id = info.get("intersection_id")
        road_segment = info.get("road_segment")
        clip_ref = info.get("clip_ref")
        self.handled += 1
        logger.info("<- info cmd=%s camera=%s road=%s prompt=%r",
                    str(command_id)[:8], camera_id, road_name, prompt[:40])

        answer, status, run_id, assigned = await self._dispatch(
            prompt=prompt, camera_id=camera_id, road_name=road_name,
            intersection_id=intersection_id, command_id=command_id,
        )
        envelope = self._build_result(
            command_id=command_id, camera_id=camera_id, road_name=road_name,
            intersection_id=intersection_id, road_segment=road_segment, clip_ref=clip_ref,
            text=answer, prompt=prompt, status=status, run_id=run_id, assigned=assigned,
        )
        try:
            await self._producer.send_and_wait(
                self.result_topic,
                key=str(command_id).encode("utf-8"),
                value=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            )
            self.emitted += 1
            logger.info("-> result cmd=%s status=%s text=%r",
                        str(command_id)[:8], status, str(answer)[:48])
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            logger.exception("emit result failed cmd=%s: %s", str(command_id)[:8], exc)

    async def _dispatch(self, *, prompt, camera_id, road_name, intersection_id, command_id):
        context = {
            "camera_id": camera_id,
            "road_name": road_name,
            "intersection_id": intersection_id,
            "command_id": command_id,
            "source": "anp_video_command",
        }
        try:
            r = await _http_json(
                "POST", self.api_base + "/api/v1/world-model/demo-dispatch",
                {"message": prompt, "context": {k: v for k, v in context.items() if v is not None}},
                timeout=60.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            logger.warning("dispatch POST failed cmd=%s: %s", str(command_id)[:8], exc)
            return ("[视频推理调度失败] %s：%s" % (road_name or camera_id or "该路段", exc), "failed", None, [])

        run_id = r.get("run_id")
        if not run_id:
            return ("[视频推理未返回 run_id]", "failed", None, [])

        loop = asyncio.get_event_loop()
        deadline = loop.time() + self.poll_timeout
        while loop.time() < deadline and not self._stop.is_set():
            await asyncio.sleep(self.poll_interval)
            try:
                g = await _http_json("GET", self.api_base + "/api/v1/world-model/demo-dispatch/" + run_id, timeout=30.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("poll err run=%s: %s", run_id, exc)
                continue
            status = g.get("status")
            if status in ("succeeded", "failed"):
                run = g.get("run") if isinstance(g.get("run"), dict) else {}
                answer = run.get("final_answer") or r.get("answer") or "(无答案)"
                assigned = [a.get("agent_id") for a in (run.get("assignments") or []) if isinstance(a, dict)]
                return (str(answer), status, run_id, assigned)
        return ("[视频推理超时] run=%s 在 %ss 内未结束" % (run_id, int(self.poll_timeout)), "timeout", run_id, [])

    def _build_result(self, *, command_id, camera_id, road_name, intersection_id,
                      road_segment, clip_ref, text, prompt, status, run_id, assigned) -> Dict[str, Any]:
        category = _derive_category(prompt, text) if status == "succeeded" else "异常"
        summary = "%s%s" % (road_name or camera_id or "该路段", category or "视频分析")
        return {
            "schema_version": "1.0",
            "message_id": "%s-%s" % (VISIONHUB_AGENT_ID, _new_id()[:12]),
            "event_type": RESULT_EVENT_TYPE,
            "source": {"system": "vision_hub", "agent_id": VISIONHUB_AGENT_ID, "service": "agents_for_vision_hub"},
            "time": {"event_ts": _now_iso(), "ingest_ts": _now_iso()},
            "scope": {
                "camera_id": camera_id,
                "road_name": road_name,
                "intersection_id": intersection_id,
                "object_id": command_id,
            },
            "payload": {
                "observation_type": "traffic.video_text",
                "camera_id": camera_id or "unknown-camera",
                "road_name": road_name,
                "intersection_id": intersection_id,
                "road_segment": road_segment,
                "text": text,
                "summary": summary,
                "category": category,
                "tags": [t for t in [category] if t],
                "entities": {"run_id": run_id, "status": status, "assigned_agents": assigned},
                "artifact_ref": clip_ref,
                "source_model": "visionhub-multi-agent",
                "confidence": 0.9 if status == "succeeded" else 0.3,
                "command_id": command_id,
            },
            "trace": {"trace_id": _new_id(), "correlation_id": command_id},
        }


async def _amain(args: argparse.Namespace) -> int:
    glue = VideoInferenceGlue(args)
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, glue._stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    await glue.start()
    try:
        await glue.run_forever(args.duration)
    finally:
        await glue.stop()
    logger.info("exit: handled=%d skipped=%d emitted=%d errors=%d",
                glue.handled, glue.skipped, glue.emitted, glue.errors)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ANP<->vision hub 视频推理胶水 sidecar（P8 step2）。")
    ap.add_argument("--bootstrap", default="localhost:9092", help="Kafka bootstrap（默认本机反向隧道）")
    ap.add_argument("--info-topic", default="visionhub.world_model.info.v1")
    ap.add_argument("--result-topic", default="edge.observation.result.v1")
    ap.add_argument("--api-base", default="http://127.0.0.1:8010", help="vision hub HTTP 基址")
    ap.add_argument("--group", default="visionhub-video-inference-glue")
    ap.add_argument("--from-beginning", action="store_true", help="从最早重放 info（默认只收新消息）")
    ap.add_argument("--poll-timeout", type=float, default=150.0, help="等单次 dispatch 完成的最大秒数")
    ap.add_argument("--poll-interval", type=float, default=3.0)
    ap.add_argument("--duration", type=float, default=None, help="运行时长（秒），默认永久")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
