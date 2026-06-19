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
from anp.video.routes import create_video_app
from anp.video.store import SqliteVideoTextStore

DATA_FILE = Path(__file__).parent / "data" / "video_text_events.json"

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
    app = create_video_app(VideoConfig(db_path=tmp_path / "api.db"))
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
