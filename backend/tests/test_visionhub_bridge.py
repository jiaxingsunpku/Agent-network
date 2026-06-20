"""vision hub 双向桥回归（P8）：命令/结果纯映射 + 桥 handle + 对账 + 入库往返。

全部不依赖 Kafka（注入假 producer）；端到端经真实 Kafka 的链路见 smoke_video_command_loop.py。
运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

from anp import contracts as c
from anp.adapters.visionhub import (
    VISIONHUB_AGENT_ID,
    VISIONHUB_PERCEPTION_AGENT_ID,
    CommandTracker,
    VisionHubBridgeConfig,
    VisionHubCommandBridge,
    VisionHubResultBridge,
    anp_command_to_visionhub_info,
    visionhub_result_to_video_text_envelope,
)
from anp.adapters.visionhub.config import VISIONHUB_INFO_TYPE, VISIONHUB_VIDEO_TEXT_EVENT_TYPE
from anp.video.retrieval import SearchFilters
from anp.video.store import SqliteVideoTextStore

ROAD = "民族大道"
CAMERA = "cam-minzu-east-001"


class FakeProducer:
    """记录 send 调用的假 producer。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, object]] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

    def flush(self):
        pass


def _video_command(command_id: str = "cmd-1", *, prompt: str = "民族大道有没有事故？", target: str = VISIONHUB_AGENT_ID):
    return c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="video-task-001"),
        target_agent_id=target,
        payload=c.CommandPayload(
            command_id=command_id,
            command_type=c.CommandType.REQUEST_VIDEO_TEXT,
            params={"camera_id": CAMERA, "road_name": ROAD, "prompt": prompt, "intersection_id": None},
        ),
        expires_at=c.expires_at_iso(300),
        object_id=ROAD,
    )


def _visionhub_result(*, correlation_id: str = "cmd-1", text: str = "民族大道（摄像头 cam）视频分析：两车追尾。", category: str = "事故"):
    """构造一条 vision hub 原生风格的文本结果 envelope（替身/真实 repo 同形）。"""

    return {
        "schema_version": "1.0",
        "message_id": "vh-msg-001",
        "event_type": VISIONHUB_VIDEO_TEXT_EVENT_TYPE,
        "source": {"system": "vision_hub", "agent_id": "video-visionhub-001"},
        "time": {"event_ts": "2026-06-13T05:30:00.000Z"},
        "scope": {"camera_id": CAMERA, "road_name": ROAD},
        "payload": {
            "camera_id": CAMERA,
            "road_name": ROAD,
            "text": text,
            "summary": f"{ROAD}{category}",
            "category": category,
            "tags": [category],
            "entities": {"vehicle_count": 12},
            "confidence": 0.9,
            "command_id": correlation_id,
        },
        "trace": {"trace_id": "vh-trace-1", "correlation_id": correlation_id},
    }


# --------------------------------------------------------------------------- #
# 命令方向：ANP 命令 → vision hub info（纯映射）
# --------------------------------------------------------------------------- #
def test_command_translate_request_video_text():
    info = anp_command_to_visionhub_info(_video_command("cmd-42"), source_agent_id="bridge-x")
    assert info is not None
    assert info["payload"]["info_type"] == VISIONHUB_INFO_TYPE
    assert info["payload"]["command_id"] == "cmd-42"
    assert info["payload"]["camera_id"] == CAMERA
    assert info["payload"]["road_name"] == ROAD
    assert info["payload"]["target_agent_id"] == VISIONHUB_AGENT_ID
    assert info["source"]["agent_id"] == "bridge-x"
    # 关联键 command_id 写入 vision hub trace.correlation_id。
    assert info["trace"]["correlation_id"] == "cmd-42"


def test_command_translate_skips_non_video_command():
    sig = c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="g"),
        target_agent_id="t",
        payload=c.CommandPayload(
            command_id="s1", command_type=c.CommandType.SET_SIGNAL_PLAN,
            params={"desired_phase": "all_red", "duration_s": 10},
        ),
        expires_at=c.expires_at_iso(30),
    )
    assert anp_command_to_visionhub_info(sig, source_agent_id="b") is None


def test_command_translate_skips_non_command():
    obs = c.observation_envelope(
        agent_id="x",
        payload=c.ObservationPayload(
            intersection_id="X",
            approaches=[c.Approach(direction=c.Direction.NORTH, vehicle_count=1, halting_count=1, mean_speed_mps=5.0)],
        ),
    )
    assert anp_command_to_visionhub_info(obs, source_agent_id="b") is None


# --------------------------------------------------------------------------- #
# 结果方向：vision hub result → ANP 视频文本事件 envelope（纯映射）
# --------------------------------------------------------------------------- #
def test_result_translate_to_video_text_envelope():
    env = visionhub_result_to_video_text_envelope(
        _visionhub_result(correlation_id="cmd-99"), perception_agent_id=VISIONHUB_PERCEPTION_AGENT_ID
    )
    assert env is not None
    assert env.event_type == c.EventType.OBSERVATION_VIDEO_TEXT
    assert env.source.agent_id == VISIONHUB_PERCEPTION_AGENT_ID
    payload = c.parse_payload(env)
    assert isinstance(payload, c.VideoTextEventPayload)
    assert payload.road_name == ROAD
    assert payload.category == "事故"
    assert env.quality.confidence == 0.9
    # correlation_id（= 原 command_id）落到 ANP envelope 的 parent_trace_id。
    assert env.trace.parent_trace_id == "cmd-99"
    # event_ts 透传 vision hub 的时间。
    assert env.time.event_ts == "2026-06-13T05:30:00.000Z"


def test_result_translate_skips_wrong_event_type():
    bad = _visionhub_result()
    bad["event_type"] = "observation.something.else"
    assert visionhub_result_to_video_text_envelope(bad, perception_agent_id="p") is None


def test_result_translate_skips_no_text():
    bad = _visionhub_result()
    bad["payload"].pop("text")
    bad["payload"].pop("summary")
    assert visionhub_result_to_video_text_envelope(bad, perception_agent_id="p") is None


def test_result_translate_handles_garbage():
    assert visionhub_result_to_video_text_envelope("not-a-dict", perception_agent_id="p") is None
    assert visionhub_result_to_video_text_envelope({"event_type": VISIONHUB_VIDEO_TEXT_EVENT_TYPE}, perception_agent_id="p") is None


# --------------------------------------------------------------------------- #
# 桥 handle + 对账
# --------------------------------------------------------------------------- #
def test_command_bridge_forwards_and_tracks():
    tracker = CommandTracker()
    bridge = VisionHubCommandBridge(VisionHubBridgeConfig(), tracker=tracker)
    producer = FakeProducer()
    info = bridge.handle(_video_command("cmd-7"), producer)
    assert info is not None and bridge.forwarded == 1
    topic, key, value = producer.sent[0]
    assert topic == VisionHubBridgeConfig().info_topic
    assert key == "cmd-7"  # 分区键 = correlation
    rec = tracker.get("cmd-7")
    assert rec is not None and rec.camera_id == CAMERA and not rec.returned


def test_command_bridge_skips_non_video():
    bridge = VisionHubCommandBridge()
    producer = FakeProducer()
    sig = c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="g"),
        target_agent_id="t",
        payload=c.CommandPayload(command_id="s1", command_type=c.CommandType.SET_SIGNAL_PLAN, params={"desired_phase": "all_red", "duration_s": 10}),
        expires_at=c.expires_at_iso(30),
    )
    assert bridge.handle(sig, producer) is None
    assert bridge.skipped == 1 and producer.sent == []


def test_result_bridge_republishes_and_tracks():
    tracker = CommandTracker()
    tracker.mark_dispatched("cmd-5", camera_id=CAMERA, road_name=ROAD)
    bridge = VisionHubResultBridge(VisionHubBridgeConfig(), tracker=tracker)
    producer = FakeProducer()
    env = bridge.handle(_visionhub_result(correlation_id="cmd-5"), producer)
    assert env is not None and bridge.republished == 1
    topic, _key, value = producer.sent[0]
    assert topic == c.VideoTopics.PERCEPTION_TEXT
    rec = tracker.get("cmd-5")
    assert rec is not None and rec.returned and rec.returned_event_id == env.message_id


def test_tracker_orphan_return_marked_known_false():
    tracker = CommandTracker()
    # 未先 dispatch → 回流标记为「孤儿」，known=False，但仍补登。
    assert tracker.mark_returned("unknown-cmd", "ev-1") is False
    assert tracker.get("unknown-cmd").returned is True
    tracker.mark_dispatched("c1")
    assert tracker.mark_returned("c1", "ev-2") is True
    assert tracker.returned_count == 2


# --------------------------------------------------------------------------- #
# 纯往返：命令 → info → (vision hub) → result → 译 → 入库 → 可检索
# --------------------------------------------------------------------------- #
def test_round_trip_command_to_store(tmp_path):
    # 命令译成 info，info.correlation 即命令 command_id。
    info = anp_command_to_visionhub_info(_video_command("rt-1"), source_agent_id="bridge")
    cid = info["trace"]["correlation_id"]
    # vision hub 产结果（correlation 回带），结果桥译回 ANP envelope。
    result = _visionhub_result(correlation_id=cid)
    env = visionhub_result_to_video_text_envelope(result, perception_agent_id=VISIONHUB_PERCEPTION_AGENT_ID)
    assert env is not None and env.trace.parent_trace_id == cid
    # P7 store 零改入库 + 检索命中。
    store = SqliteVideoTextStore(tmp_path / "v.db")
    assert store.append(env) is True
    hits = store.search(SearchFilters(road_name=ROAD, limit=10))
    assert hits and hits[0]["category"] == "事故"
    assert hits[0]["event_id"] == env.message_id
