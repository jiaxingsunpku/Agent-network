"""网关 FastAPI 应用 —— 五接口，纯读模型 + 命令入口（docs/gateway-api.md）。

路由前缀 ``/api/agent-network``，全部返回 application/json（前端据此判断回落 mock）。
读接口：snapshot / projection / timeseries(*) / edge-inference；写接口：commands。
鉴权默认关闭，开启后读接口需 read token、命令需 admin token（Bearer）。

应用本身不算世界状态，只读 :class:`GatewayState` 的当前态与 registry，并把命令构造、
发布到控制层 topic（AGENTS.md §3.3/§3.4）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import mapping
from .commands import CommandValidationError, build_command_envelope
from .config import GatewayConfig
from .models import CommandResponse, CommandTarget
from .state import GatewayState, PublishFailed, PublishUnavailable

API_PREFIX = "/api/agent-network"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": {"code": code, "message": message}})


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip()


def create_app(state: GatewayState | None = None) -> FastAPI:
    """构造网关应用。``state`` 缺省新建（带默认 registry 种子，无 producer）。"""

    state = state or GatewayState()
    cfg: GatewayConfig = state.config
    app = FastAPI(title="ANP Gateway", version="0.1.0")
    app.state.gw = state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 开发期放开；前端 vite 与网关多为不同源
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- 鉴权（默认关闭）-------------------------------------------------- #
    def require_read(request: Request) -> JSONResponse | None:
        if not cfg.require_auth:
            return None
        token = _bearer(request)
        allowed = {t for t in (cfg.read_token, cfg.admin_token) if t}
        if token and token in allowed:
            return None
        return _error(401, "unauthorized", "需要有效的 read token")

    def require_admin(request: Request) -> JSONResponse | None:
        if not cfg.require_auth:
            return None
        token = _bearer(request)
        if cfg.admin_token and token == cfg.admin_token:
            return None
        return _error(401, "unauthorized", "命令需要有效的 admin token")

    # -- 读：snapshot ----------------------------------------------------- #
    @app.get(API_PREFIX + "/snapshot")
    def get_snapshot(request: Request, scope: str | None = None) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        snap = mapping.build_snapshot(state)
        return JSONResponse(content=snap.model_dump(mode="json"))

    # -- 读：projection --------------------------------------------------- #
    @app.get(API_PREFIX + "/projection")
    def get_projection(request: Request, kind: str = "world_model", id: str = "traffic") -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        proj = mapping.build_projection(state, kind, id)
        return JSONResponse(content=proj.model_dump(mode="json"))

    # -- 写：commands ----------------------------------------------------- #
    @app.post(API_PREFIX + "/commands")
    async def post_command(request: Request) -> JSONResponse:
        denied = require_admin(request)
        if denied:
            return denied
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            return _error(400, "invalid_body", "请求体不是合法 JSON")
        try:
            env, command_id = build_command_envelope(raw, state.registry)
        except CommandValidationError as exc:
            return _error(exc.http_status, exc.code, exc.message)

        try:
            message_id = state.publish_command(env)
        except PublishUnavailable as exc:
            return _error(503, "kafka_unavailable", str(exc))
        except PublishFailed as exc:
            return _error(500, "publish_failed", str(exc))

        # 审计账本（不存 token / 完整 payload）。
        state.command_log.record_command(
            command_id=command_id,
            command_type=env.payload["command_type"],
            target_agent_id=env.target.agent_id or "",
            object_id=env.scope.object_id,
            region_id=env.scope.region_id,
            issued_at=env.time.event_ts,
        )
        resp = CommandResponse(
            command_id=command_id,
            topic=state.command_topic,
            target=CommandTarget(agent_id=env.target.agent_id, region_id=env.target.region_id),
            status="published",
            message_id=message_id,
        )
        return JSONResponse(content=resp.model_dump(mode="json"))

    # -- 读：edge-inference（本期不支持，结构化失败）---------------------- #
    @app.post(API_PREFIX + "/edge-inference")
    async def post_edge_inference(request: Request) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            raw = {}
        agent_id = raw.get("agent_id") if isinstance(raw, dict) else None
        mode = (raw.get("mode") if isinstance(raw, dict) else None) or "auto"
        return JSONResponse(
            content={
                "ok": False,
                "agent_id": agent_id,
                "mode": mode,
                "error": {"code": "unsupported", "message": "edge inference 未在本期交通域启用"},
            }
        )

    # -- 读：timeseries（冷路径未启用，结构化失败）------------------------ #
    @app.get(API_PREFIX + "/timeseries/{name}")
    def get_timeseries(request: Request, name: str) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        return JSONResponse(
            content={"ok": False, "error": {"code": "timeseries_disabled", "message": "cold path not enabled in v1"}}
        )

    # -- 运维健康检查（非契约接口）--------------------------------------- #
    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse(content={"ok": True, "service": "anp-gateway"})

    return app
