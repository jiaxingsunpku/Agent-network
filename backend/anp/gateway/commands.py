"""命令入口校验 + envelope 构造（docs/gateway-api.md §3、protocol.md §5/§7）。

网关**只校验命令外形 + 白名单**，不做业务安全判定（如 set_signal_plan 的相位/时长
范围）——那是执行端本地 Safety Guard 的职责（protocol.md §7）。这里：
1. target_agent_id 必填（400）；
2. 拒绝 broadcast / agent_ids（400）；
3. command_type 是合法枚举（400）；
4. 目标在白名单且接收该命令类型（403，由 registry 裁决）；
5. expires_in_sec 合法（400）；
6. 构造 command envelope（target.agent_id、time.expires_at），交调用方发布。
"""

from __future__ import annotations

from ..contracts import (
    CommandPayload,
    CommandType,
    Envelope,
    Scope,
    Source,
    SourceSystem,
    command_envelope,
    expires_at_iso,
    new_message_id,
)
from ..registry import Registry
from .config import (
    DEFAULT_COMMAND_EXPIRES_SEC,
    GATEWAY_AGENT_ID,
    GATEWAY_ID,
    MAX_COMMAND_EXPIRES_SEC,
    MIN_COMMAND_EXPIRES_SEC,
)

#: 前端禁止的群发字段（前端本地也拦，网关二次兜底，protocol.md §7）。
_FORBIDDEN_KEYS = ("broadcast", "agent_ids")
_VALID_COMMAND_TYPES = {ct.value for ct in CommandType}


class CommandValidationError(Exception):
    """命令校验失败，携带 HTTP 状态码与统一错误体的 code/message。"""

    def __init__(self, http_status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message


def build_command_envelope(raw: dict, registry: Registry) -> tuple[Envelope, str]:
    """校验请求并构造命令 envelope；返回 ``(envelope, command_id)``。

    校验不通过抛 :class:`CommandValidationError`。``raw`` 是前端原始请求体 dict
    （直接用 dict 以完全掌控错误码/错误体格式，避免 FastAPI 默认 422）。
    """

    if not isinstance(raw, dict):
        raise CommandValidationError(400, "invalid_body", "请求体必须是 JSON 对象")

    # 1) 群发字段拦截（先于其它，给最明确的拒绝理由）。
    for key in _FORBIDDEN_KEYS:
        if key in raw:
            raise CommandValidationError(400, "broadcast_not_allowed", f"禁止 {key}：命令必须指定单一 target_agent_id")

    # 2) target_agent_id 必填。
    target_agent_id = raw.get("target_agent_id")
    if not isinstance(target_agent_id, str) or not target_agent_id.strip():
        raise CommandValidationError(400, "missing_target_agent_id", "target_agent_id 必填")
    target_agent_id = target_agent_id.strip()

    # 3) command_type 合法枚举。
    command_type = raw.get("command_type")
    if not isinstance(command_type, str) or command_type not in _VALID_COMMAND_TYPES:
        raise CommandValidationError(
            400, "invalid_command_type", f"command_type 非法，本期支持: {sorted(_VALID_COMMAND_TYPES)}"
        )

    # 4) 白名单：目标已注册且接收该命令类型。
    authz = registry.authorize_command(target_agent_id, command_type)
    if not authz.allowed:
        raise CommandValidationError(403, authz.code or "forbidden", authz.message or "命令目标未授权")

    # 5) expires_in_sec 范围。
    expires_in = raw.get("expires_in_sec", DEFAULT_COMMAND_EXPIRES_SEC)
    if expires_in is None:
        expires_in = DEFAULT_COMMAND_EXPIRES_SEC
    try:
        expires_in = float(expires_in)
    except (TypeError, ValueError):
        raise CommandValidationError(400, "invalid_expires_in_sec", "expires_in_sec 必须是数值")
    if not (MIN_COMMAND_EXPIRES_SEC <= expires_in <= MAX_COMMAND_EXPIRES_SEC):
        raise CommandValidationError(
            400,
            "invalid_expires_in_sec",
            f"expires_in_sec 须在 [{MIN_COMMAND_EXPIRES_SEC}, {MAX_COMMAND_EXPIRES_SEC}]",
        )

    # 6) 构造 envelope（params 原样透传，业务安全交执行端 Safety Guard）。
    params = raw.get("payload") or {}
    if not isinstance(params, dict):
        raise CommandValidationError(400, "invalid_payload", "payload 必须是 JSON 对象")

    command_id = new_message_id()
    message_id = new_message_id()
    payload = CommandPayload(command_id=command_id, command_type=CommandType(command_type), params=params)
    env = command_envelope(
        source=Source(system=SourceSystem.PLATFORM, agent_id=GATEWAY_AGENT_ID, gateway_id=GATEWAY_ID),
        target_agent_id=target_agent_id,
        payload=payload,
        expires_at=expires_at_iso(expires_in),
        site_id=raw.get("site_id"),
        region_id=raw.get("region_id"),
        object_id=raw.get("object_id"),
        message_id=message_id,
    )
    return env, command_id
