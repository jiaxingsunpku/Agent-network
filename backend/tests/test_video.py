"""视频文本事件问答（P7）测试：契约入库、检索、规则问答、HTTP API、ingest 幂等。

全部不依赖 Kafka 与外部 LLM：QA 用禁用 LLM 的配置走规则摘要兜底。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anp.video.config import LLMConfig, VideoConfig
from anp.video.ingest import ingest_value
from anp.video.models import VideoTextEventIn
from anp.video.qa import VideoQAService
from anp.video.retrieval import (
    SearchFilters,
    extract_category,
    extract_filters,
    extract_keywords,
    extract_road,
)
from anp.adapters.visionhub.catalog import derive_intersection_id, map_camera
from anp.video.routes import create_video_app
from anp.video.store import SqliteVideoTextStore

DATA_FILE = Path(__file__).parent / "data" / "video_text_events.json"

# 摄像头/路口目录对齐 wangxuan（step1）样例：2 路口（2+1 cam）+ 1 孤儿。
_SAMPLE_WX_CAMERAS = [
    {"source_id": 23, "camera_id": "-2024-08-07_a", "name": "和平路东北角", "district": "天津和平",
     "intersection_name": "和平路与哈密道", "primary_road": "和平路", "secondary_road": "哈密道",
     "camera_position": "东北角", "status": "completed", "latitude": None, "longitude": None},
    {"source_id": 24, "camera_id": "-2024-08-07_b", "name": "和平路西南角", "district": "天津和平",
     "intersection_name": "和平路与哈密道", "primary_road": "和平路", "secondary_road": "哈密道",
     "camera_position": "西南角", "status": "completed", "latitude": None, "longitude": None},
    {"source_id": 1, "camera_id": "-2024-07_c", "name": "桂林路东北角", "district": "天津和平",
     "intersection_name": "桂林路与成都道", "primary_road": "桂林路", "secondary_road": "成都道",
     "camera_position": "东北角", "status": "completed", "latitude": None, "longitude": None},
    {"source_id": 99, "camera_id": "orphan", "name": "孤儿源", "district": None,
     "intersection_name": None, "primary_road": None, "secondary_road": None,
     "camera_position": None, "status": "completed", "latitude": None, "longitude": None},
]

#: 禁用 LLM 的配置 → QA 走规则摘要，单测不出网。
_NO_LLM = LLMConfig(base_url=None, model="x", api_key=None, proxy=None)


def _sample_events() -> list[dict]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _store_with_samples(tmp_path: Path) -> SqliteVideoTextStore:
    store = SqliteVideoTextStore(tmp_path / "v.db")
    for ev in _sample_events():
        env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id="video-perception-001")
        store.append(env)
    return store


# --------------------------------------------------------------------------- #
# store
# --------------------------------------------------------------------------- #
def test_store_append_idempotent_and_count(tmp_path):
    store = SqliteVideoTextStore(tmp_path / "v.db")
    ev = _sample_events()[0]
    env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id="video-perception-001")
    assert store.append(env) is True
    # 同一 message_id 再投递 → 去重，不新增
    assert store.append(env) is False
    assert store.count() == 1
    got = store.get(env.message_id)
    assert got is not None
    assert got["road_name"] == "民族大道"
    assert got["tags"] == ["事故", "追尾", "占道"]


def test_store_search_time_filter(tmp_path):
    store = _store_with_samples(tmp_path)
    # 6 月 13 日下午（UTC 04:00–09:00 ≈ 当地 12:00–17:00）
    hits = store.search(
        SearchFilters(time_from="2026-06-13T04:00:00Z", time_to="2026-06-13T09:00:00Z", limit=50)
    )
    ts = [h["event_ts"] for h in hits]
    # 命中当日下午的事故/拥堵/抛锚；排除前一天、上午、夜间、次日
    assert "2026-06-13T05:30:00.000Z" in ts
    assert "2026-06-12T05:30:00.000Z" not in ts  # 前一天
    assert "2026-06-13T01:05:00.000Z" not in ts  # 上午
    assert "2026-06-14T05:30:00.000Z" not in ts  # 次日
    # 倒序（最近优先）
    assert ts == sorted(ts, reverse=True)


def test_store_search_road_category_keyword(tmp_path):
    store = _store_with_samples(tmp_path)
    # 路段 + 类别
    hits = store.search(SearchFilters(road_name="民族大道", category="事故", limit=50))
    assert hits and all(h["road_name"] == "民族大道" for h in hits)
    assert all("事故" in (h.get("category") or "") for h in hits)
    # 关键词命中正文
    hits2 = store.search(SearchFilters(keywords=["闯红灯"], limit=50))
    assert len(hits2) == 1 and hits2[0]["category"] == "违章"
    # 路段隔离：雄楚大道不应混入民族大道结果
    hits3 = store.search(SearchFilters(road_name="民族大道", limit=50))
    assert all("雄楚" not in (h.get("road_name") or "") for h in hits3)


# --------------------------------------------------------------------------- #
# retrieval 解析
# --------------------------------------------------------------------------- #
def test_extract_road_strips_time_phrase():
    assert extract_road("6月13号下午民族大道有没有事故？") == "民族大道"
    assert extract_road("雄楚大道现在堵不堵") == "雄楚大道"
    assert extract_road("今天怎么样") is None


def test_extract_keywords_and_category():
    assert "事故" in extract_keywords("民族大道有没有事故")
    assert extract_category("民族大道有没有事故") == "事故"
    assert extract_category("今天天气如何") is None


def test_extract_filters_explicit_priority_no_auto_keyword_with_road():
    base = SearchFilters(road_name="雄楚大道", time_from="2026-06-13T00:00:00Z")
    f = extract_filters("民族大道有没有事故", base)
    # 显式 road 优先，不被问题里的「民族大道」覆盖
    assert f.road_name == "雄楚大道"
    # 有路段过滤时不自动加关键词硬过滤（避免漏召回），category 也不自动设
    assert f.keywords == []
    assert f.category is None
    assert f.time_from == "2026-06-13T00:00:00Z"


def test_extract_filters_auto_keyword_only_without_space_filter():
    # 无路段/路口/显式 keyword → 用问题类别词收窄宽问题
    f = extract_filters("今天哪里有事故", SearchFilters())
    assert f.road_name is None
    assert "事故" in f.keywords
    # 显式 keywords 始终生效
    f2 = extract_filters("民族大道情况", SearchFilters(road_name="民族大道", keywords=["施工"]))
    assert f2.keywords == ["施工"]


# --------------------------------------------------------------------------- #
# QA（规则摘要兜底，无 LLM）
# --------------------------------------------------------------------------- #
def test_qa_rule_summary_hits(tmp_path):
    store = _store_with_samples(tmp_path)
    qa = VideoQAService(store, llm_config=_NO_LLM)
    res = qa.answer(
        "6月13号下午民族大道有没有事故？",
        base_filters=SearchFilters(
            time_from="2026-06-13T04:00:00Z", time_to="2026-06-13T09:00:00Z", road_name="民族大道"
        ),
    )
    assert res["evidence"], "应召回证据"
    assert any(e["category"] == "事故" for e in res["evidence"])
    assert res["tool_calls"][0]["tool"] == "search_video_text_events"
    assert "LLM 未启用" in " ".join(res["warnings"])
    assert "民族大道" in res["answer"]


def test_qa_no_hits(tmp_path):
    store = _store_with_samples(tmp_path)
    qa = VideoQAService(store, llm_config=_NO_LLM)
    res = qa.answer(
        "光谷大道有没有事故",
        base_filters=SearchFilters(road_name="光谷大道"),
    )
    assert res["evidence"] == []
    assert "未检索到" in res["answer"]
    assert "未召回相关视频文本事件" in res["warnings"]


# --------------------------------------------------------------------------- #
# ingest 幂等（不经 Kafka）
# --------------------------------------------------------------------------- #
def test_ingest_value_idempotent_and_filtering(tmp_path):
    store = SqliteVideoTextStore(tmp_path / "v.db")
    ev = _sample_events()[0]
    env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id="video-perception-001")
    wire = env.to_wire()
    assert ingest_value(store, wire) == "new"
    assert ingest_value(store, wire) == "duplicate"
    # 非视频文本事件 / 垃圾消息 → skipped
    assert ingest_value(store, {"not": "an envelope"}) == "skipped"
    assert store.count() == 1


# --------------------------------------------------------------------------- #
# HTTP API（TestClient；临时库 + 禁用 LLM）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 禁用 LLM：清空 OPENAI_*，使 from_env().enabled = False（不出网）
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app = create_video_app(VideoConfig(db_path=tmp_path / "api.db", task_db_path=tmp_path / "tasks.db"))
    return TestClient(app)


def test_api_ingest_then_query(client):
    # 入库一条
    ev = _sample_events()[0]
    r = client.post("/api/agent-network/video-text/events", json=ev)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is True and body["count"] == 1
    # 同体再入 → 不去重（HTTP 每次新 message_id），count 增长；改用幂等需带 message_id
    r2 = client.post("/api/agent-network/video-text/events", json={**ev, "message_id": body["event_id"]})
    assert r2.json()["stored"] is False  # 显式同 message_id → 去重

    # 查询
    q = {
        "question": "民族大道有没有事故",
        "road_name": "民族大道",
        "category": "事故",
    }
    rq = client.post("/api/agent-network/video-text/query", json=q)
    assert rq.status_code == 200, rq.text
    qb = rq.json()
    assert qb["evidence"] and qb["evidence"][0]["road_name"] == "民族大道"
    assert qb["tool_calls"][0]["tool"] == "search_video_text_events"
    assert "answer" in qb and "warnings" in qb


def test_api_ingest_validation_error(client):
    # 缺 text → 422
    r = client.post("/api/agent-network/video-text/events", json={"camera_id": "c"})
    assert r.status_code == 422


def test_api_health(client):
    r = client.get("/api/agent-network/video-text/health")
    assert r.status_code == 200
    assert r.json()["service"] == "anp-video-text"


# --------------------------------------------------------------------------- #
# HTTP API：位置枚举 + 数据库浏览 + 取单条（task2）
# --------------------------------------------------------------------------- #
_VTX = "/api/agent-network/video-text"


def _seed_via_http(client) -> int:
    """把全部样例事件经 HTTP 入库（各自新 message_id，8 条内容互异不去重）。"""

    for ev in _sample_events():
        r = client.post(f"{_VTX}/events", json=ev)
        assert r.status_code == 200, r.text
    return client.get(f"{_VTX}/health").json()["count"]


def test_api_locations(client):
    assert _seed_via_http(client) == 8
    r = client.get(f"{_VTX}/locations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_events"] == 8
    inters = {i["intersection_id"]: i for i in body["intersections"]}
    assert set(inters) == {"gg-xiongchu-minzu", "gg-xiongchu-luxiang"}
    # 民族大道路口：east(5) + west(1)，事件数 6
    minzu = inters["gg-xiongchu-minzu"]
    assert minzu["road_name"] == "民族大道"
    assert minzu["event_count"] == 6
    cams = {c["camera_id"]: c["event_count"] for c in minzu["cameras"]}
    assert cams == {"cam-minzu-east-001": 5, "cam-minzu-west-002": 1}
    # 雄楚大道路口：north(2)
    luxiang = inters["gg-xiongchu-luxiang"]
    assert luxiang["event_count"] == 2
    assert luxiang["cameras"][0]["camera_id"] == "cam-xiongchu-north-003"
    # 路口按事件数倒序：民族大道(6) 在 雄楚大道(2) 前
    assert body["intersections"][0]["intersection_id"] == "gg-xiongchu-minzu"


# --- 摄像头/路口目录对齐 wangxuan（step1）------------------------------------ #
def test_catalog_map_pure():
    assert derive_intersection_id(None) is None
    assert derive_intersection_id("  ") is None
    assert derive_intersection_id("和平路与哈密道") == derive_intersection_id("和平路与哈密道")
    assert derive_intersection_id("和平路与哈密道").startswith("vh-")
    rec = map_camera(_SAMPLE_WX_CAMERAS[0], "2026-06-22T00:00:00Z")
    assert rec["source_id"] == 23 and rec["camera_position"] == "东北角"
    assert rec["intersection_id"] == derive_intersection_id("和平路与哈密道")
    assert rec["district"] == "天津和平" and rec["synced_at"] == "2026-06-22T00:00:00Z"
    # 孤儿（无路口名）→ intersection_id 为 None
    assert map_camera(_SAMPLE_WX_CAMERAS[3], "t")["intersection_id"] is None


def test_catalog_store_replace_and_locations(tmp_path):
    store = SqliteVideoTextStore(tmp_path / "v.db")
    recs = [map_camera(r, "2026-06-22T00:00:00Z") for r in _SAMPLE_WX_CAMERAS]
    assert store.replace_cameras(recs) == 4
    assert store.camera_count() == 4
    # 全量替换语义：再次写入不累加
    store.replace_cameras(recs)
    assert store.camera_count() == 4
    locs = store.catalog_locations()
    names = [g["intersection_name"] for g in locs]
    assert names == ["和平路与哈密道", "桂林路与成都道", None]  # 摄像头数倒序 + 孤儿置末
    hp = locs[0]
    assert hp["road_name"] == "和平路" and hp["district"] == "天津和平"
    assert hp["intersection_id"] == derive_intersection_id("和平路与哈密道")
    assert len(hp["cameras"]) == 2
    cam = hp["cameras"][0]
    assert cam["source_id"] in (23, 24) and cam["camera_position"] in ("东北角", "西南角")
    assert locs[-1]["intersection_id"] is None and len(locs[-1]["cameras"]) == 1  # 孤儿


def test_api_locations_catalog_overrides_and_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    db = tmp_path / "api.db"
    # 预置目录（对齐 wangxuan）
    store = SqliteVideoTextStore(db)
    store.replace_cameras([map_camera(r, "2026-06-22T00:00:00Z") for r in _SAMPLE_WX_CAMERAS])
    app = create_video_app(VideoConfig(db_path=db, task_db_path=tmp_path / "t.db"))
    client = TestClient(app)
    # 再经 HTTP 灌入 8 条文本事件（民族大道/雄楚——目录未覆盖，应并入不丢）
    assert _seed_via_http(client) == 8
    body = client.get(f"{_VTX}/locations").json()
    by_name = {i["intersection_name"]: i for i in body["intersections"] if i["intersection_name"]}
    assert "和平路与哈密道" in by_name  # 目录路口
    hp = by_name["和平路与哈密道"]
    assert hp["district"] == "天津和平" and hp["intersection_id"].startswith("vh-")
    assert hp["cameras"][0]["source_id"] is not None
    # 文本事件位置（目录未覆盖）仍在
    by_id = {i["intersection_id"]: i for i in body["intersections"]}
    assert "gg-xiongchu-minzu" in by_id and by_id["gg-xiongchu-minzu"]["event_count"] == 6


def _make_event(*, camera_id: str, text: str, intersection_id: str | None = None,
                road_name: str | None = None, event_ts: str = "2026-06-22T05:00:00Z") -> dict:
    """构造一条带指定真身标识的视频文本事件 dict（供 store/HTTP 入库）。"""

    return {
        "camera_id": camera_id, "intersection_id": intersection_id, "road_name": road_name,
        "text": text, "event_ts": event_ts,
    }


def test_catalog_locations_event_count_alignment(tmp_path):
    """对齐 step2：回流事件按 camera_id（相机级）/ intersection_id（路口级）挂到目录。"""

    store = SqliteVideoTextStore(tmp_path / "v.db")
    store.replace_cameras([map_camera(r, "2026-06-22T00:00:00Z") for r in _SAMPLE_WX_CAMERAS])
    hp_inter = derive_intersection_id("和平路与哈密道")

    def _ingest(ev: dict) -> None:
        env = VideoTextEventIn.model_validate(ev).to_envelope(default_agent_id="video-perception-visionhub-001")
        assert store.append(env) is True

    # ① 命中具体目录相机（camera_id=-2024-08-07_a，带真路口）。
    _ingest(_make_event(camera_id="-2024-08-07_a", intersection_id=hp_inter, road_name="和平路", text="两车追尾"))
    # ② 同路口「所有摄像头」：camera_id 退化为 unknown-camera，但带真 intersection_id。
    _ingest(_make_event(camera_id="unknown-camera", intersection_id=hp_inter, road_name="和平路",
                        text="路口拥堵", event_ts="2026-06-22T05:01:00Z"))
    # ③ 无 intersection_id，但 camera_id 属另一目录相机（西南角）→ 经相机回溯到同路口。
    _ingest(_make_event(camera_id="-2024-08-07_b", text="违章闯红灯", event_ts="2026-06-22T05:02:00Z"))
    # ④ 合成 id（武汉），与天津目录不通 → 不挂任何目录路口/相机。
    _ingest(_make_event(camera_id="cam-minzu-east-001", intersection_id="gg-xiongchu-minzu",
                        road_name="民族大道", text="正常", event_ts="2026-06-22T05:03:00Z"))

    locs = {g["intersection_id"]: g for g in store.catalog_locations()}
    hp = locs[hp_inter]
    # 路口级：①②③ 三条都归到和平路（②靠 intersection_id、③靠相机回溯、①两者皆可，去重计一次）。
    assert hp["event_count"] == 3
    cams = {c["camera_id"]: c for c in hp["cameras"]}
    assert cams["-2024-08-07_a"]["event_count"] == 1   # 相机级：① 精确命中
    assert cams["-2024-08-07_b"]["event_count"] == 1   # 相机级：③ 精确命中
    # 合成事件 ④ 不污染目录任何路口。
    桂林 = locs[derive_intersection_id("桂林路与成都道")]
    assert 桂林["event_count"] == 0
    # 孤儿组事件数仍为 0（无事件命中 orphan 相机）。
    assert locs[None]["event_count"] == 0


def test_store_get_camera_by_source_id(tmp_path):
    store = SqliteVideoTextStore(tmp_path / "v.db")
    store.replace_cameras([map_camera(r, "2026-06-22T00:00:00Z") for r in _SAMPLE_WX_CAMERAS])
    cam = store.get_camera(23)
    assert cam is not None and cam["camera_id"] == "-2024-08-07_a"
    assert cam["intersection_id"] == derive_intersection_id("和平路与哈密道")
    assert store.get_camera(999999) is None


def test_event_sync_map_and_alignment(tmp_path):
    """对齐 step3：真身 events 行经目录映射成 ANP 文本事件，回填后挂到正确相机/路口。"""

    from anp.adapters.visionhub.catalog import map_event

    store = SqliteVideoTextStore(tmp_path / "v.db")
    store.replace_cameras([map_camera(r, "2026-06-22T00:00:00Z") for r in _SAMPLE_WX_CAMERAS])
    source_map = store.camera_source_index()
    assert source_map[23]["camera_id"] == "-2024-08-07_a"

    # 真身 events 行样例（轻字段）：source_id=23 命中目录 和平路东北角。
    row = {
        "id": 1001, "source_id": 23, "event_type": "speeding_vehicle", "severity": "high",
        "description": "检测到疑似超速车辆 track_id=30193。", "detected_at": "2026-05-30T19:44:15.625741+08:00",
        "confidence": None, "track_ids": [30193],
    }
    rec = map_event(row, source_map)
    assert rec["message_id"] == "vh-evt-1001"           # 幂等键派生真身 id
    assert rec["camera_id"] == "-2024-08-07_a"          # 经 source_id 对齐目录相机
    assert rec["intersection_id"] == derive_intersection_id("和平路与哈密道")
    assert rec["category"] == "超速" and "high" in rec["tags"]
    assert rec["entities"]["source_event_id"] == 1001 and rec["entities"]["track_ids"] == [30193]
    assert rec["confidence"] is None                    # 越界/缺失 confidence 置空（VideoTextEventIn ge0/le1）

    # 回填入库（VideoTextEventIn 路径）→ catalog_locations 挂到 和平路。
    env = VideoTextEventIn.model_validate(rec).to_envelope(default_agent_id=rec["source_agent_id"])
    assert store.append(env) is True
    assert store.append(env) is False  # message_id 幂等
    locs = {g["intersection_id"]: g for g in store.catalog_locations()}
    hp = locs[derive_intersection_id("和平路与哈密道")]
    assert hp["event_count"] == 1
    assert {c["camera_id"]: c["event_count"] for c in hp["cameras"]}["-2024-08-07_a"] == 1

    # source_id 不在目录 → 合成 camera_id、不挂目录、不抛异常。
    orphan = map_event({"id": 7, "source_id": 88888, "event_type": "traffic_congestion",
                        "description": "拥堵", "detected_at": "2026-05-30T12:00:00+08:00"}, source_map)
    assert orphan["camera_id"] == "vh-source-88888" and orphan["intersection_id"] is None
    assert orphan["category"] == "拥堵"


def test_api_browse_by_intersection_and_paging(client):
    _seed_via_http(client)
    # 整路口（跨 east+west）共 6 条
    r = client.get(f"{_VTX}/events", params={"intersection_id": "gg-xiongchu-minzu", "limit": 50})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 6
    assert len(body["items"]) == 6
    assert all(it["intersection_id"] == "gg-xiongchu-minzu" for it in body["items"])
    # 列表项不带 envelope（减重）
    assert all(it["envelope"] is None for it in body["items"])
    # 倒序（最近优先）
    ts = [it["event_ts"] for it in body["items"]]
    assert ts == sorted(ts, reverse=True)
    # 翻页：limit=4 + offset=4 → total 仍 6，剩 2 条，且与首页无重叠
    p1 = client.get(f"{_VTX}/events", params={"intersection_id": "gg-xiongchu-minzu", "limit": 4, "offset": 0}).json()
    p2 = client.get(f"{_VTX}/events", params={"intersection_id": "gg-xiongchu-minzu", "limit": 4, "offset": 4}).json()
    assert p1["total"] == 6 and p2["total"] == 6
    assert len(p1["items"]) == 4 and len(p2["items"]) == 2
    ids1 = {it["event_id"] for it in p1["items"]}
    ids2 = {it["event_id"] for it in p2["items"]}
    assert ids1.isdisjoint(ids2)


def test_api_browse_by_camera_and_keyword(client):
    _seed_via_http(client)
    # 收窄到单摄像头
    r = client.get(f"{_VTX}/events", params={"camera_id": "cam-minzu-east-001", "limit": 50})
    body = r.json()
    assert body["total"] == 5
    assert all(it["camera_id"] == "cam-minzu-east-001" for it in body["items"])
    # 关键词 q 命中正文（闯红灯 → 违章，西进口那条）
    rk = client.get(f"{_VTX}/events", params={"q": "闯红灯", "limit": 50}).json()
    assert rk["total"] == 1
    assert rk["items"][0]["category"] == "违章"


def test_api_get_event_detail_and_404(client):
    _seed_via_http(client)
    # 取一条已知记录（先从浏览拿 event_id）
    first = client.get(f"{_VTX}/events", params={"camera_id": "cam-minzu-east-001", "category": "事故"}).json()
    assert first["items"], first
    eid = first["items"][0]["event_id"]
    r = client.get(f"{_VTX}/events/{eid}")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["event_id"] == eid
    assert rec["text"]  # 全文
    assert isinstance(rec["tags"], list) and rec["tags"]
    assert rec["envelope"] is not None  # 详情带 envelope
    assert rec["source_model"] == "vlm-traffic-v1"
    # 不存在 → 404
    assert client.get(f"{_VTX}/events/does-not-exist").status_code == 404
