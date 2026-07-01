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
from pydantic import BaseModel, Field, ValidationError

from ..contracts import (
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    Channel,
    EventType,
    Source,
    SourceSystem,
    WorldTopics,
    make_envelope,
)
from ..messaging import publish
from . import mapping
from .commands import CommandValidationError, build_command_envelope
from .config import GatewayConfig
from .models import CommandResponse, CommandTarget
from .state import GatewayState, PublishFailed, PublishUnavailable

API_PREFIX = "/api/agent-network"


class RegistrationChannelIn(BaseModel):
    """Operator supplied channel declaration for a world registration profile."""

    topic: str = Field(min_length=1)
    keys: list[str] = Field(default_factory=list)


class RegistrationAgentIn(BaseModel):
    """One agent declaration in an operator-managed registration profile."""

    agent_id: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    command_types: list[str] = Field(default_factory=list)
    produces: list[RegistrationChannelIn] = Field(default_factory=list)
    consumes: list[RegistrationChannelIn] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0.0)
    members: list[str] = Field(default_factory=list)
    status: str = Field(default="online", min_length=1)
    last_error: str | None = None


class AgentRegistrationRequest(BaseModel):
    """Custom world registration profile submitted from the operator console.

    The target model is advisory: model ownership is still derived from the
    registered topic/key contracts so the frontend cannot fake affiliation.
    """

    source: str | None = None
    target_model_id: str | None = None
    agents: list[RegistrationAgentIn] = Field(min_length=1)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _safe_int(value, fallback=None):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return fallback


def _channels(channels: list[RegistrationChannelIn]) -> list[Channel]:
    return [Channel(topic=ch.topic.strip(), keys=_dedupe(ch.keys)) for ch in channels]


def _world_source(agent_id: str) -> Source:
    # Use the registered agent id as the partition key so compacted lifecycle
    # topics retain one latest record per agent even when submitted by an admin.
    return Source(system=SourceSystem.PLATFORM, agent_id=agent_id, gateway_id="anp-gateway")


def _registration_envelopes(agent: RegistrationAgentIn, produces: list[Channel], consumes: list[Channel]):
    source = _world_source(agent.agent_id)
    lifecycle = make_envelope(
        event_type=EventType.AGENT_REGISTERED,
        source=source,
        payload=AgentLifecyclePayload(
            agent_id=agent.agent_id,
            agent_type=agent.agent_type,
            capabilities=_dedupe(agent.capabilities),
            command_types=_dedupe(agent.command_types),
            produces=produces,
            consumes=consumes,
            weight=agent.weight,
            members=_dedupe(agent.members),
        ),
    )
    heartbeat = make_envelope(
        event_type=EventType.AGENT_HEARTBEAT,
        source=source,
        payload=AgentHeartbeatPayload(status=agent.status, last_error=agent.last_error),
    )
    return (
        (WorldTopics.AGENT_LIFECYCLE, lifecycle),
        (WorldTopics.AGENT_HEARTBEAT, heartbeat),
    )


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

    # -- 读：world（统一世界总览：跨域 agent + model + catalog）----------- #
    @app.get(API_PREFIX + "/world")
    def get_world(request: Request) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        return JSONResponse(content=mapping.build_world(state))

    # -- 读：overview（交通域全局总览：系统级共识 + SV 仿真元信息，task5 P-10）-- #
    @app.get(API_PREFIX + "/overview")
    def get_overview(request: Request) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        body = state.overview()
        if cfg.with_consumers and cfg.sv_base_url:
            from anp.adapters.signalvision import SignalVisionClient

            status = SignalVisionClient(cfg.sv_base_url, timeout_sec=0.8).get_status()
            if status.ok:
                sv = status.body
                running = bool(sv.get("running", False))
                body["running"] = running
                if sv.get("config"):
                    body["algorithm"] = sv.get("config")
                if sv.get("current_time") is not None:
                    body["sim_step"] = _safe_int(sv.get("current_time"), body.get("sim_step"))
                if sv.get("total_time") is not None:
                    body["total_steps"] = _safe_int(sv.get("total_time"), body.get("total_steps"))
        return JSONResponse(content={"ok": True, **body})

    # -- 读：intersection/{id}（单路口 World Status，前端侧栏详情，task5 P-10）-- #
    @app.get(API_PREFIX + "/intersection/{intersection_id}")
    def get_intersection(request: Request, intersection_id: str) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        st = state.status_store.all().get(intersection_id)
        if st is None:
            return JSONResponse(
                content={"ok": False, "error": {"code": "not_found", "message": f"路口 {intersection_id} 暂无 World Status"}}
            )
        return JSONResponse(content={"ok": True, "intersection": st.model_dump(mode="json")})

    # -- 读：sv-network（真实 SV 路网几何，前端画真图）-------------------- #
    # 务实例外：网关唯一一处直连外部源 HTTP（SV /api/network），只读几何、不入 Kafka 黑板；
    # SV 原生结构的解析在 adapter（build_road_geometry），网关只搬运。同步 def → 线程池跑阻塞 IO。
    @app.get(API_PREFIX + "/sv-network")
    def get_sv_network(request: Request) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        from anp.adapters.signalvision import SignalVisionClient
        from anp.adapters.signalvision.network import build_road_geometry

        client = SignalVisionClient(cfg.sv_base_url, timeout_sec=4.0)
        net = client.get_network()
        if not net.ok:
            return _error(503, "sv_unreachable", f"SignalVision 路网不可达: {net.body.get('message', '')}")
        summary = client.get_junctions_summary()
        geo = build_road_geometry(net.body, summary.body if summary.ok else {})
        return JSONResponse(content={"ok": True, "source": "signalvision", **geo})

    # -- 读：sv-maps（SV 可用地图列表，前端切图下拉用）------------------- #
    @app.get(API_PREFIX + "/sv-maps")
    def get_sv_maps(request: Request) -> JSONResponse:
        denied = require_read(request)
        if denied:
            return denied
        from anp.adapters.signalvision import SignalVisionClient

        client = SignalVisionClient(cfg.sv_base_url, timeout_sec=4.0)
        resp = client.list_maps()
        if not resp.ok:
            return _error(503, "sv_unreachable", f"SignalVision 地图列表不可达: {resp.body.get('message', '')}")
        maps = resp.body.get("maps") if isinstance(resp.body.get("maps"), list) else []
        return JSONResponse(content={"ok": True, "maps": maps, "count": len(maps)})

    # -- 写：registrations（操作台自定义接入世界智能体）--------------------- #
    @app.post(API_PREFIX + "/registrations")
    async def post_registration(request: Request) -> JSONResponse:
        denied = require_admin(request)
        if denied:
            return denied
        try:
            raw = await request.json()
        except Exception:  # noqa: BLE001
            return _error(400, "invalid_body", "请求体不是合法 JSON")
        try:
            req = AgentRegistrationRequest.model_validate(raw)
        except ValidationError as exc:
            return _error(400, "invalid_registration", exc.errors()[0].get("msg", "注册声明不合法"))

        prepared: list[tuple[RegistrationAgentIn, list[Channel], list[Channel]]] = []
        for agent in req.agents:
            prepared.append((agent, _channels(agent.produces), _channels(agent.consumes)))

        persistence = "registry_only"
        if state.producer is not None:
            try:
                for agent, produces, consumes in prepared:
                    for topic, env in _registration_envelopes(agent, produces, consumes):
                        future = publish(state.producer, topic, env, flush=True)
                        future.get(timeout=5.0)
                persistence = "world_topics"
            except Exception as exc:  # noqa: BLE001 - kafka-python 异常类型较散
                return _error(500, "registration_publish_failed", str(exc))

        now = state.now()
        for agent, produces, consumes in prepared:
            state.registry.register(
                agent_id=agent.agent_id,
                agent_type=agent.agent_type,
                capabilities=_dedupe(agent.capabilities),
                command_types=_dedupe(agent.command_types),
                produces=produces,
                consumes=consumes,
                weight=agent.weight,
                members=_dedupe(agent.members),
                now=now,
            )
            state.registry.heartbeat(
                agent_id=agent.agent_id,
                status=agent.status,
                last_error=agent.last_error,
                now=now,
            )

        return JSONResponse(
            content={
                "ok": True,
                "source": req.source,
                "target_model_id": req.target_model_id,
                "registered": [agent.agent_id for agent, _, _ in prepared],
                "persistence": persistence,
                "world": mapping.build_world(state),
            }
        )

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
