"""视频文本问答 HTTP 路由（P7）。

前缀 ``/api/agent-network/video-text``（与网关同命名空间，便于前端复用反代）：
- ``POST /events``：入库一条视频文本事件；
- ``POST /query``：检索 + 问答，返回对齐老前端的 ``QueryResponse``；
- ``GET  /health``：库内条数 + LLM 是否启用。

逻辑全在 ``anp/video/`` 包内；可挂到网关进程（:func:`include_video_routes`）或独立运行
（:func:`create_video_app`）。不混入交通域世界状态计算（AGENTS.md §3.4）。
"""

from __future__ import annotations

from fastapi import APIRouter

from .config import VideoConfig, get_video_config
from .models import IngestResponse, QueryResponse, VideoTextEventIn, VideoTextQueryRequest
from .qa import VideoQAService
from .retrieval import SearchFilters
from .store import SqliteVideoTextStore, VideoTextStore

VIDEO_API_PREFIX = "/api/agent-network/video-text"


def build_default_services(config: VideoConfig | None = None) -> tuple[VideoTextStore, VideoQAService]:
    config = config or get_video_config()
    store = SqliteVideoTextStore(config.db_path)
    qa = VideoQAService(store)
    return store, qa


def create_video_router(
    store: VideoTextStore,
    qa: VideoQAService,
    *,
    config: VideoConfig | None = None,
) -> APIRouter:
    config = config or get_video_config()
    router = APIRouter(prefix=VIDEO_API_PREFIX, tags=["video-text"])

    @router.post("/events", response_model=IngestResponse)
    def ingest_event(event: VideoTextEventIn) -> IngestResponse:
        env = event.to_envelope(default_agent_id=config.perception_agent_id)
        stored = store.append(env)
        return IngestResponse(event_id=env.message_id, stored=stored, count=store.count())

    @router.post("/query", response_model=QueryResponse)
    def query(req: VideoTextQueryRequest) -> QueryResponse:
        limit = req.limit or config.default_query_limit
        base = SearchFilters(
            time_from=req.time_from,
            time_to=req.time_to,
            road_name=req.road_name,
            intersection_id=req.intersection_id,
            camera_id=req.camera_id,
            category=req.category,
            keywords=list(req.keywords),
            limit=limit,
        )
        result = qa.answer(req.question, base_filters=base, limit=limit)
        return QueryResponse(**result)

    @router.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "service": "anp-video-text",
            "count": store.count(),
            "llm_enabled": VideoConfig.llm().enabled,
        }

    return router


def include_video_routes(app, *, config: VideoConfig | None = None) -> tuple[VideoTextStore, VideoQAService]:
    """把视频文本路由挂到已有 FastAPI 应用（如网关进程）。"""

    config = config or get_video_config()
    store, qa = build_default_services(config)
    app.include_router(create_video_router(store, qa, config=config))
    app.state.video_store = store
    app.state.video_qa = qa
    return store, qa


def create_video_app(config: VideoConfig | None = None):
    """独立运行视频文本问答服务（run_video_qa.py 用）。"""

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    config = config or get_video_config()
    app = FastAPI(title="ANP Video Text QA", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    include_video_routes(app, config=config)
    return app
