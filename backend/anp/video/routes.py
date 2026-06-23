"""视频文本问答 HTTP 路由（P7）。

前缀 ``/api/agent-network/video-text``（与网关同命名空间，便于前端复用反代）：
- ``POST /events``：入库一条视频文本事件；
- ``POST /query``：检索 + 问答，返回对齐老前端的 ``QueryResponse``；
- ``GET  /health``：库内条数 + LLM 是否启用；
- ``POST /tasks``：新建协作视频任务（编排器扇出定向命令，P9）；
- ``GET  /tasks`` / ``GET /tasks/{id}``：任务列表 / 详情（纯读 + 聚合，P9）；
- ``GET  /command-modules``：命令模块声明枚举（前端区分可下发/占位，P9）。

逻辑全在 ``anp/video/`` 包内；可挂到网关进程（:func:`include_video_routes`）或独立运行
（:func:`create_video_app`）。不混入交通域世界状态计算、**不在网关算聚合**（AGENTS.md §3.4，
聚合在编排器/任务体侧）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .command_modules import list_command_modules
from .config import VideoConfig, get_video_config
from .models import (
    CameraFacet,
    CommandModuleOut,
    EventBrowseOut,
    EventRecordOut,
    IngestResponse,
    IntersectionFacet,
    LocationsOut,
    QueryResponse,
    TaskCreateRequest,
    VideoTextEventIn,
    VideoTextQueryRequest,
)
from .orchestrator import (
    CommandModuleError,
    NoTargetsError,
    PublishUnavailable,
    VideoTaskOrchestrator,
)
from .qa import VideoQAService
from .retrieval import SearchFilters
from .store import SqliteVideoTextStore, VideoTextStore
from .tasks import SqliteVideoTaskStore, VideoTask

VIDEO_API_PREFIX = "/api/agent-network/video-text"


def build_default_services(config: VideoConfig | None = None) -> tuple[VideoTextStore, VideoQAService]:
    config = config or get_video_config()
    store = SqliteVideoTextStore(config.db_path)
    qa = VideoQAService(store)
    return store, qa


def create_video_router(
    store: VideoTextStore,
    qa: VideoQAService,
    orchestrator: VideoTaskOrchestrator,
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

    # -- 位置枚举 + 数据库浏览（task2 派生 / step1 目录对齐）：纯读，不出网 -- #
    @router.get("/locations", response_model=LocationsOut)
    def locations() -> LocationsOut:
        """供前端位置选择器的「路口 → 摄像头」层级。

        摄像头/路口目录（``video_cameras``，由 adapter 同步自 wangxuan，1:1）非空时**优先**
        从目录出（富化 intersection_name/district/camera_position/source_id）；再并入文本库
        派生但目录未覆盖的位置（保留 ANP 自有事件位置不丢）。目录为空则纯文本派生（兼容 task2）。
        """

        def _key(inter_id: str | None, road: str | None) -> tuple:
            if inter_id:
                return ("i", inter_id)
            if road:
                return ("r", road)
            return ("u",)

        intersections: list[IntersectionFacet] = []
        seen: set[tuple] = set()

        # 1) 目录（对齐 wangxuan）优先。
        for loc in store.catalog_locations():
            seen.add(_key(loc.get("intersection_id"), loc.get("road_name")))
            intersections.append(
                IntersectionFacet(
                    intersection_id=loc.get("intersection_id"),
                    intersection_name=loc.get("intersection_name"),
                    road_name=loc.get("road_name"),
                    district=loc.get("district"),
                    event_count=loc.get("event_count", 0),
                    cameras=[
                        CameraFacet(
                            camera_id=cam["camera_id"],
                            source_id=cam.get("source_id"),
                            name=cam.get("name"),
                            camera_position=cam.get("camera_position"),
                            event_count=cam["event_count"],
                        )
                        for cam in loc.get("cameras", [])
                    ],
                )
            )

        # 2) 文本库派生但目录未覆盖的位置（ANP 自有事件不丢）。
        for loc in store.distinct_locations():
            if _key(loc.get("intersection_id"), loc.get("road_name")) in seen:
                continue
            intersections.append(
                IntersectionFacet(
                    intersection_id=loc.get("intersection_id"),
                    road_name=loc.get("road_name"),
                    event_count=loc.get("event_count", 0),
                    cameras=[
                        CameraFacet(camera_id=cam["camera_id"], event_count=cam["event_count"])
                        for cam in loc.get("cameras", [])
                    ],
                )
            )

        return LocationsOut(intersections=intersections, total_events=store.count())

    @router.get("/events", response_model=EventBrowseOut)
    def browse_events(
        limit: int | None = Query(default=None, ge=1, le=config.max_query_limit),
        offset: int = Query(default=0, ge=0),
        intersection_id: str | None = None,
        camera_id: str | None = None,
        road_name: str | None = None,
        category: str | None = None,
        q: str | None = Query(default=None, description="关键词（匹配正文/摘要/类别/路名）"),
        time_from: str | None = None,
        time_to: str | None = None,
    ) -> EventBrowseOut:
        """分页 + 过滤浏览库记录（列表项不带 envelope，减重）。"""

        page_limit = limit or config.default_query_limit
        filters = SearchFilters(
            time_from=time_from,
            time_to=time_to,
            road_name=road_name,
            intersection_id=intersection_id,
            camera_id=camera_id,
            category=category,
            keywords=[q] if q else [],
            limit=page_limit,
            offset=offset,
        )
        rows, total = store.browse(filters)
        return EventBrowseOut(
            total=total,
            limit=page_limit,
            offset=offset,
            items=[EventRecordOut.from_row(r) for r in rows],
        )

    @router.get("/events/{event_id}", response_model=EventRecordOut)
    def get_event(event_id: str) -> EventRecordOut:
        """取单条完整记录（含 tags/entities/envelope）。"""

        row = store.get(event_id)
        if row is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        return EventRecordOut.from_row(row, with_envelope=True)

    # -- 协作视频任务（P9）：创建（扇出）+ 纯读列表/详情 ------------------ #
    @router.post("/tasks", response_model=VideoTask)
    def create_task(req: TaskCreateRequest) -> VideoTask:
        try:
            return orchestrator.create_task(req.prompt, req.scope, module=req.module)
        except (CommandModuleError, NoTargetsError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except PublishUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @router.get("/tasks", response_model=list[VideoTask])
    def list_tasks(limit: int = 50) -> list[VideoTask]:
        # 批量本地刷新回流状态；全部回流时缓存规则摘要，不调用 LLM。
        return orchestrator.list_tasks(limit=limit)

    @router.get("/tasks/{task_id}", response_model=VideoTask)
    def get_task(
        task_id: str,
        llm: bool = Query(default=False, description="是否重新调用 LLM 精炼聚合答案"),
    ) -> VideoTask:
        task = orchestrator.refresh_task(task_id, aggregate=True, use_llm=llm)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @router.get("/command-modules", response_model=list[CommandModuleOut])
    def command_modules() -> list[CommandModuleOut]:
        return [
            CommandModuleOut(
                key=m.key,
                title=m.title,
                description=m.description,
                implemented=m.implemented,
                command_type=m.command_type.value if m.command_type else None,
            )
            for m in list_command_modules()
        ]

    return router


def build_orchestrator(
    store: VideoTextStore,
    qa: VideoQAService,
    *,
    config: VideoConfig | None = None,
    producer=None,
    command_bootstrap: str | None = None,
) -> VideoTaskOrchestrator:
    """构造任务编排器（任务存储 + 视频命令 producer）。

    ``producer`` 注入优先（网关进程复用其 producer 发 ``anp.video.command.v1``）；缺省时编排器
    按 ``command_bootstrap`` 懒构造（独立服务 / CLI）。视频命令直发视频控制层，不经网关交通管道。
    """

    config = config or get_video_config()
    task_store = SqliteVideoTaskStore(config.task_db_path)
    return VideoTaskOrchestrator(
        task_store=task_store, text_store=store, qa=qa, producer=producer, bootstrap=command_bootstrap
    )


def include_video_routes(
    app,
    *,
    config: VideoConfig | None = None,
    producer=None,
    command_bootstrap: str | None = None,
) -> tuple[VideoTextStore, VideoQAService, VideoTaskOrchestrator]:
    """把视频文本/任务路由挂到已有 FastAPI 应用（如网关进程）。"""

    config = config or get_video_config()
    store, qa = build_default_services(config)
    orchestrator = build_orchestrator(
        store, qa, config=config, producer=producer, command_bootstrap=command_bootstrap
    )
    app.include_router(create_video_router(store, qa, orchestrator, config=config))
    app.state.video_store = store
    app.state.video_qa = qa
    app.state.video_orchestrator = orchestrator
    return store, qa, orchestrator


def create_video_app(
    config: VideoConfig | None = None,
    *,
    producer=None,
    command_bootstrap: str | None = None,
):
    """独立运行视频文本问答 + 任务服务（run_video_qa.py 用）。"""

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    config = config or get_video_config()
    app = FastAPI(title="ANP Video Text QA", version="0.1.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    include_video_routes(app, config=config, producer=producer, command_bootstrap=command_bootstrap)
    return app
