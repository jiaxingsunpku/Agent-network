"""SignalVision Dashboard HTTP API 薄客户端。

只覆盖**感知接入**所需的只读端点（不含 sv.inference.* 控制，那是后续「信号控制」
任务）。用标准库 ``urllib`` 实现，不引入新依赖（AGENTS.md §5.3）。借鉴老仓库
``signalvision_kafka_bridge.py`` 的 HTTP 封装思想，但只留只读、拆干净。

真实端点（见 ~/project/SignalVision/dashboard/server.py）：
  - ``GET /api/simulation/status``：仿真运行状态（心跳用：可达 + running）。
  - ``GET /api/junctions/<junction_id>``：单路口完整状态（含 incoming_lanes / metrics）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class SvResponse:
    """一次 SV HTTP 调用的结果。``ok`` 表示传输层成功且 2xx。"""

    ok: bool
    status_code: int | None
    body: dict[str, Any]


class SignalVisionClient:
    """SV Dashboard 只读 HTTP 客户端。"""

    def __init__(self, base_url: str, *, timeout_sec: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    # -- 底层请求 ---------------------------------------------------------- #
    def _get(self, path: str) -> SvResponse:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                body = json.loads(raw) if raw else {}
                return SvResponse(ok=True, status_code=resp.status, body=body if isinstance(body, dict) else {})
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"message": text}
            return SvResponse(ok=False, status_code=exc.code, body=parsed if isinstance(parsed, dict) else {})
        except Exception as exc:  # noqa: BLE001 - 网络/超时/解析等都归一为不可达
            return SvResponse(ok=False, status_code=None, body={"message": str(exc)})

    # -- 端点 -------------------------------------------------------------- #
    def get_status(self) -> SvResponse:
        """仿真运行状态（心跳用）。"""

        return self._get("/api/simulation/status")

    def get_junction_detail(self, junction_id: str) -> SvResponse:
        """单路口完整状态（原始返回，含 ``{"junction": {...}, "success": ...}``）。"""

        return self._get(f"/api/junctions/{junction_id}")

    def junction_state(self, junction_id: str) -> dict[str, Any] | None:
        """取并解包单路口状态字典；不可达 / 不存在 / 失败时返回 ``None``。"""

        resp = self.get_junction_detail(junction_id)
        if not resp.ok or not resp.body.get("success"):
            return None
        junction = resp.body.get("junction")
        return junction if isinstance(junction, dict) else None
