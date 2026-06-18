"""网关配置 —— 环境变量、常量、鉴权开关（docs/gateway-api.md 通用约定 / §3）。

网关是纯读模型 + 命令入口，配置项尽量少。鉴权默认关闭；开启后读接口需 read token、
命令需 admin token（Bearer）。所有时间阈值集中在此。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: 命令源（envelope.source.agent_id）：网关作为平台侧命令发起方。
GATEWAY_AGENT_ID = "traffic-gateway-001"
GATEWAY_ID = "gateway-001"

#: 命令默认有效期（秒）：前端未传 expires_in_sec 时用。
DEFAULT_COMMAND_EXPIRES_SEC = 30.0
#: 命令有效期允许范围（秒），超出按 400 入参非法。
MIN_COMMAND_EXPIRES_SEC = 1.0
MAX_COMMAND_EXPIRES_SEC = 300.0

#: World Status 超过此时长（秒）未更新 → 路口节点判定为离线（docs/gateway-api.md §1.1）。
STATUS_STALE_SEC = 30.0

#: 命令/ack 审计与展示的内存环形缓冲容量。
COMMAND_LOG_CAPACITY = 200
#: snapshot.trend 环形缓冲容量。
TREND_CAPACITY = 30


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class GatewayConfig:
    """网关运行配置，来自环境变量（带默认值）。"""

    bootstrap: str | None = None
    require_auth: bool = field(default_factory=lambda: _truthy(os.environ.get("AGENT_NETWORK_REQUIRE_AUTH")))
    read_token: str | None = field(default_factory=lambda: os.environ.get("AGENT_NETWORK_READ_TOKEN") or None)
    admin_token: str | None = field(default_factory=lambda: os.environ.get("AGENT_NETWORK_ADMIN_TOKEN") or None)
    #: 是否启动后台 Kafka 消费线程（状态/ack/registry）。测试用 False。
    with_consumers: bool = True

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        return cls(bootstrap=os.environ.get("ANP_BOOTSTRAP") or None)
