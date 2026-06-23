"""协作视频任务编排回归（P9）：命令模块注册表 + Task 存储 + 编排器扇出/归因/聚合。

全部不依赖 Kafka（注入假 producer + 临时 SQLite）；端到端经真实 Kafka 的链路见
backend/scripts/smoke_video_task.py。运行：``/home/sjx/miniconda3/envs/anp/bin/python -m pytest``（cwd = backend/）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from anp import contracts as c
from anp.video.command_modules import REQUEST_VIDEO_TEXT, get_command_module, list_command_modules
from anp.video.config import LLMConfig, VideoConfig
from anp.video.orchestrator import (
    DEFAULT_VISIONHUB_ROSTER,
    CommandModuleError,
    NoTargetsError,
    VideoTaskOrchestrator,
)
from anp.video.qa import VideoQAService
from anp.video.retrieval import SearchFilters
from anp.video.routes import create_video_router
from anp.video.store import SqliteVideoTextStore
from anp.video.tasks import SqliteVideoTaskStore, TaskScope

ROAD = "民族大道"
CAMERA = "cam-minzu-east-001"
_NO_LLM = LLMConfig(base_url=None, model="x", api_key=None, proxy=None)


class FakeProducer:
    """记录 send 调用的假 producer（视频命令直发，无 Kafka）。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, object]] = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
        return None

    def flush(self):
        pass


def _make_orch(tmp_path, *, producer=None):
    text_store = SqliteVideoTextStore(tmp_path / "video_text.db")
    task_store = SqliteVideoTaskStore(tmp_path / "video_tasks.db")
    qa = VideoQAService(text_store, llm_config=_NO_LLM)
    orch = VideoTaskOrchestrator(
        task_store=task_store, text_store=text_store, qa=qa, producer=producer or FakeProducer()
    )
    return orch, text_store, task_store


def _ingest_returned_text(store: SqliteVideoTextStore, *, command_id: str, text: str, road=ROAD, camera=CAMERA):
    """模拟结果桥回流入库：parent_trace_id == 原命令 command_id。"""

    env = c.video_text_envelope(
        agent_id="video-perception-visionhub-001",
        payload=c.VideoTextEventPayload(camera_id=camera, road_name=road, text=text, category="事故"),
        trace=c.Trace(trace_id=c.new_trace_id(), parent_trace_id=command_id),
    )
    store.append(env)
    return env.message_id


# --------------------------------------------------------------------------- #
# 命令模块注册表
# --------------------------------------------------------------------------- #
def test_command_module_registry():
    mod = get_command_module(REQUEST_VIDEO_TEXT)
    assert mod is not None and mod.implemented and mod.command_type == c.CommandType.REQUEST_VIDEO_TEXT
    # 占位模块声明但未落地执行端。
    for key in ("video.detect", "video.stream.attach", "video.model.select"):
        m = get_command_module(key)
        assert m is not None and not m.implemented and m.command_type is None
    assert get_command_module("不存在") is None
    assert {m.key for m in list_command_modules()} >= {REQUEST_VIDEO_TEXT, "video.detect"}


# --------------------------------------------------------------------------- #
# 目标体筛选
# --------------------------------------------------------------------------- #
def test_select_targets(tmp_path):
    orch, _, _ = _make_orch(tmp_path)
    assert orch.select_targets(TaskScope(road_name=ROAD), REQUEST_VIDEO_TEXT) == list(DEFAULT_VISIONHUB_ROSTER)
    # 显式目标优先，保序去重。
    assert orch.select_targets(TaskScope(target_agent_ids=["a", "b", "a"]), REQUEST_VIDEO_TEXT) == ["a", "b"]
    # roster 为空且无显式 → 报错。
    orch.roster = ()
    with pytest.raises(NoTargetsError):
        orch.select_targets(TaskScope(road_name=ROAD), REQUEST_VIDEO_TEXT)


# --------------------------------------------------------------------------- #
# 扇出：定向命令、禁群发
# --------------------------------------------------------------------------- #
def test_create_task_fans_out_directed_commands(tmp_path):
    producer = FakeProducer()
    orch, _, task_store = _make_orch(tmp_path, producer=producer)
    hubs = ["video-visionhub-a-001", "video-visionhub-b-001"]
    task = orch.create_task("多机看一下", TaskScope(road_name=ROAD, camera_id=CAMERA, target_agent_ids=hubs))

    assert task.status == "running"
    assert len(task.commands) == 2
    assert {cmd.target_agent_id for cmd in task.commands} == set(hubs)
    assert len({cmd.command_id for cmd in task.commands}) == 2  # 唯一 command_id
    # 持久化。
    assert task_store.get(task.task_id).task_id == task.task_id

    # 实际发到控制层的 wire：每条带单一 target.agent_id、无群发字段。
    assert len(producer.sent) == 2
    seen_targets = set()
    for topic, _key, wire in producer.sent:
        assert topic == c.VideoTopics.COMMAND
        assert wire["event_type"] == "command"
        tgt = wire["target"]["agent_id"]
        assert tgt in hubs
        seen_targets.add(tgt)
        assert "broadcast" not in wire and "agent_ids" not in wire
        assert "broadcast" not in wire["target"] and "agent_ids" not in wire["target"]
        # 命令类型走契约白名单。
        assert wire["payload"]["command_type"] == c.CommandType.REQUEST_VIDEO_TEXT.value
    assert seen_targets == set(hubs)


def test_create_task_default_single_hub(tmp_path):
    orch, _, _ = _make_orch(tmp_path)
    task = orch.create_task(f"{ROAD}有没有事故？", TaskScope(road_name=ROAD, camera_id=CAMERA))
    assert len(task.commands) == 1
    assert task.commands[0].target_agent_id == DEFAULT_VISIONHUB_ROSTER[0]


def test_create_task_rejects_unimplemented_module(tmp_path):
    orch, _, _ = _make_orch(tmp_path)
    with pytest.raises(CommandModuleError):
        orch.create_task("检测一下", TaskScope(road_name=ROAD), module="video.detect")
    with pytest.raises(CommandModuleError):
        orch.create_task("x", TaskScope(road_name=ROAD), module="不存在")
    with pytest.raises(CommandModuleError):
        orch.create_task("   ", TaskScope(road_name=ROAD))  # 空 prompt


# --------------------------------------------------------------------------- #
# 收集 + 聚合：按 command_id 归因
# --------------------------------------------------------------------------- #
def test_refresh_attributes_by_command_id_and_aggregates(tmp_path):
    orch, text_store, _ = _make_orch(tmp_path)
    task = orch.create_task(f"{ROAD}有没有事故？", TaskScope(road_name=ROAD, camera_id=CAMERA))
    cid = task.commands[0].command_id

    # 未回流前刷新：仍 running、无证据。
    r0 = orch.refresh_task(task.task_id)
    assert r0.status == "running" and r0.returned_count == 0

    # 回流入库（parent_trace_id == command_id）后刷新：归因 + 聚合。
    event_id = _ingest_returned_text(text_store, command_id=cid, text="民族大道两车追尾，右车道受阻。")
    r1 = orch.refresh_task(task.task_id, aggregate=True)
    assert r1.status == "aggregated"
    assert r1.returned_count == 1
    assert r1.commands[0].status == "returned"
    assert r1.commands[0].returned_event_id == event_id
    assert r1.answer and r1.evidence
    assert event_id in [e.get("event_id") for e in r1.evidence]


def test_refresh_list_marks_aggregated_with_local_answer(tmp_path):
    """列表刷新：全部命令回流即标 aggregated，并缓存本地摘要（不出网）。"""

    orch, text_store, _ = _make_orch(tmp_path)
    task = orch.create_task(f"{ROAD}有没有事故？", TaskScope(road_name=ROAD, camera_id=CAMERA))
    cid = task.commands[0].command_id
    _ingest_returned_text(text_store, command_id=cid, text="民族大道两车追尾。")
    # list_tasks 批量刷新：状态翻 aggregated（全部回流），命令 returned，并缓存本地摘要。
    listed = {t.task_id: t for t in orch.list_tasks()}
    r = listed[task.task_id]
    assert r.status == "aggregated"
    assert r.commands[0].status == "returned"
    assert r.answer and r.evidence
    assert any("本地规则摘要" in w for w in r.warnings)


def test_refresh_ignores_other_commands_events(tmp_path):
    """别的命令的回流文本不得被误归因到本任务（精确按 command_id，非按内容/路段）。"""

    orch, text_store, _ = _make_orch(tmp_path)
    task = orch.create_task(f"{ROAD}有没有事故？", TaskScope(road_name=ROAD, camera_id=CAMERA))
    # 同路段、同内容，但 parent_trace_id 是别的命令 → 不应归因到本任务。
    _ingest_returned_text(text_store, command_id="someone-else-cmd", text="民族大道两车追尾。")
    r = orch.refresh_task(task.task_id, aggregate=True)
    assert r.returned_count == 0
    assert r.status == "running"


def test_refresh_unknown_task_returns_none(tmp_path):
    orch, _, _ = _make_orch(tmp_path)
    assert orch.refresh_task("no-such-task") is None


# --------------------------------------------------------------------------- #
# store 归因列：parent_trace_id 精确过滤
# --------------------------------------------------------------------------- #
def test_store_parent_trace_id_filter(tmp_path):
    store = SqliteVideoTextStore(tmp_path / "v.db")
    _ingest_returned_text(store, command_id="CMD-A", text="A 路段事故")
    _ingest_returned_text(store, command_id="CMD-B", text="B 路段拥堵", road="雄楚大道")
    hits_a = store.search(SearchFilters(parent_trace_ids=["CMD-A"]))
    assert len(hits_a) == 1 and hits_a[0]["parent_trace_id"] == "CMD-A"
    hits_both = store.search(SearchFilters(parent_trace_ids=["CMD-A", "CMD-B"]))
    assert len(hits_both) == 2
    assert store.search(SearchFilters(parent_trace_ids=["CMD-X"])) == []


# --------------------------------------------------------------------------- #
# 任务存储往返
# --------------------------------------------------------------------------- #
def test_task_store_roundtrip(tmp_path):
    orch, _, task_store = _make_orch(tmp_path)
    t1 = orch.create_task("一", TaskScope(road_name=ROAD, camera_id=CAMERA))
    t2 = orch.create_task("二", TaskScope(road_name=ROAD, camera_id="cam-2"))
    assert task_store.count() == 2
    got = task_store.get(t1.task_id)
    assert got.prompt == "一" and got.scope.camera_id == CAMERA
    listed = task_store.list()
    assert {t.task_id for t in listed} == {t1.task_id, t2.task_id}


# --------------------------------------------------------------------------- #
# 网关任务路由（TestClient，FakeProducer，无 Kafka）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def task_client(tmp_path):
    orch, text_store, _ = _make_orch(tmp_path, producer=FakeProducer())
    app = FastAPI()
    app.include_router(create_video_router(text_store, orch.qa, orch, config=VideoConfig(db_path=tmp_path / "x.db")))
    return TestClient(app), orch, text_store


def test_route_create_list_get_task(task_client):
    client, orch, text_store = task_client
    # 创建任务（扇出 1 条定向命令）。
    r = client.post(
        "/api/agent-network/video-text/tasks",
        json={"prompt": f"{ROAD}有没有事故？", "scope": {"road_name": ROAD, "camera_id": CAMERA}},
    )
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] == "running" and len(task["commands"]) == 1
    cid = task["commands"][0]["command_id"]
    tid = task["task_id"]

    # 列表（纯读，未回流仍 running）。
    rl = client.get("/api/agent-network/video-text/tasks")
    assert rl.status_code == 200
    assert any(t["task_id"] == tid for t in rl.json())

    # 回流入库后取详情 → 聚合。
    _ingest_returned_text(text_store, command_id=cid, text="民族大道两车追尾。")
    rd = client.get(f"/api/agent-network/video-text/tasks/{tid}")
    assert rd.status_code == 200
    detail = rd.json()
    assert detail["status"] == "aggregated"
    assert detail["commands"][0]["status"] == "returned"
    assert detail["answer"] and detail["evidence"]


def test_route_get_task_defaults_to_local_summary_not_llm(tmp_path, monkeypatch):
    """详情默认快路径：即使 LLM 已配置，也不应阻塞在外部模型。"""

    def fail_chat(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("default task detail must not call LLM")

    monkeypatch.setattr("anp.video.qa.chat", fail_chat)
    text_store = SqliteVideoTextStore(tmp_path / "video_text.db")
    task_store = SqliteVideoTaskStore(tmp_path / "video_tasks.db")
    qa = VideoQAService(
        text_store,
        llm_config=LLMConfig(base_url="http://llm.invalid/v1", model="x", api_key="key", proxy=None),
    )
    orch = VideoTaskOrchestrator(task_store=task_store, text_store=text_store, qa=qa, producer=FakeProducer())
    app = FastAPI()
    app.include_router(create_video_router(text_store, qa, orch, config=VideoConfig(db_path=tmp_path / "x.db")))
    client = TestClient(app)

    task = orch.create_task(f"{ROAD}有没有事故？", TaskScope(road_name=ROAD, camera_id=CAMERA))
    event_id = _ingest_returned_text(text_store, command_id=task.commands[0].command_id, text="民族大道两车追尾。")
    resp = client.get(f"/api/agent-network/video-text/tasks/{task.task_id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["status"] == "aggregated"
    assert detail["answer"]
    assert event_id in [e["event_id"] for e in detail["evidence"]]
    assert any("本地规则摘要" in w for w in detail["warnings"])


def test_route_create_task_bad_module_400(task_client):
    client, _, _ = task_client
    r = client.post(
        "/api/agent-network/video-text/tasks",
        json={"prompt": "检测一下", "module": "video.detect", "scope": {"road_name": ROAD}},
    )
    assert r.status_code == 400
    r2 = client.post(
        "/api/agent-network/video-text/tasks",
        json={"prompt": "x", "module": "不存在", "scope": {"road_name": ROAD}},
    )
    assert r2.status_code == 400


def test_route_get_unknown_task_404(task_client):
    client, _, _ = task_client
    assert client.get("/api/agent-network/video-text/tasks/nope").status_code == 404


def test_route_command_modules(task_client):
    client, _, _ = task_client
    r = client.get("/api/agent-network/video-text/command-modules")
    assert r.status_code == 200
    mods = {m["key"]: m for m in r.json()}
    assert mods[REQUEST_VIDEO_TEXT]["implemented"] is True
    assert mods["video.detect"]["implemented"] is False
    assert mods[REQUEST_VIDEO_TEXT]["command_type"] == "request_video_text"
