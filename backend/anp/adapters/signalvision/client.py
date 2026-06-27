"""SignalVision Dashboard HTTP API 薄客户端。

覆盖**感知接入**（只读）与 **P6 信号控制**（写）所需端点。用标准库 ``urllib`` 实现，
不引入新依赖（AGENTS.md §5.3）。借鉴老仓库 ``signalvision_kafka_bridge.py`` 的 HTTP
封装思想，但拆干净、契约统一。

真实端点（见 ~/project/SignalVision/dashboard/server.py）：
  - ``GET  /api/simulation/status``：仿真运行状态（心跳用：可达 + running）。
  - ``GET  /api/junctions/<junction_id>``：单路口完整状态（含 incoming_lanes / metrics）。
  - ``POST /api/junctions/<junction_id>/update``：写入 ``traffic_light``（信号控制，P6 执行侧用）
    / ``lane_data``。注：P5 文档写的 ``sv.inference.*`` 是占位概念，真实 SV 无此端点。
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

    def _post(self, path: str, body: dict[str, Any]) -> SvResponse:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                return SvResponse(ok=True, status_code=resp.status, body=parsed if isinstance(parsed, dict) else {})
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")[:500]
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"message": text}
            return SvResponse(ok=False, status_code=exc.code, body=parsed if isinstance(parsed, dict) else {})
        except Exception as exc:  # noqa: BLE001 - 网络/超时/解析等都归一为失败
            return SvResponse(ok=False, status_code=None, body={"message": str(exc)})

    # -- 端点 -------------------------------------------------------------- #
    def get_status(self) -> SvResponse:
        """仿真运行状态（心跳用）。"""

        return self._get("/api/simulation/status")

    def get_junction_detail(self, junction_id: str) -> SvResponse:
        """单路口完整状态（原始返回，含 ``{"junction": {...}, "success": ...}``）。"""

        return self._get(f"/api/junctions/{junction_id}")

    def get_network(self) -> SvResponse:
        """整张路网几何（``GET /api/network`` → ``{junction_count, network_data{edge,node,inter,...}}``）。"""

        return self._get("/api/network")

    def list_maps(self) -> SvResponse:
        """可用地图列表（``GET /api/maps`` → ``{maps:[{name,path,size}], count, success}``）。"""

        return self._get("/api/maps")

    def get_junctions_summary(self) -> SvResponse:
        """全部路口摘要（``GET /api/junctions/summary`` → ``{summaries:[{junction_id,position,congestion_level,...}]}``）。"""

        return self._get("/api/junctions/summary")

    def junction_state(self, junction_id: str) -> dict[str, Any] | None:
        """取并解包单路口状态字典；不可达 / 不存在 / 失败时返回 ``None``。"""

        resp = self.get_junction_detail(junction_id)
        if not resp.ok or not resp.body.get("success"):
            return None
        junction = resp.body.get("junction")
        return junction if isinstance(junction, dict) else None

    # -- 写端点（P6 信号控制）-------------------------------------------- #
    def update_junction(
        self,
        junction_id: str,
        *,
        traffic_light: dict[str, Any] | None = None,
        lane_data: dict[str, Any] | None = None,
    ) -> SvResponse:
        """``POST /api/junctions/<id>/update`` 写信号灯 / 车道数据（信号控制执行侧）。

        ``ok=True`` 表示传输层成功且 SV 返回 ``success``；否则视为执行失败（执行端回 FAILED）。
        """

        body: dict[str, Any] = {}
        if traffic_light is not None:
            body["traffic_light"] = traffic_light
        if lane_data is not None:
            body["lane_data"] = lane_data
        resp = self._post(f"/api/junctions/{junction_id}/update", body)
        # SV update 端点成功时返回 {"success": true, ...}；HTTP 2xx 但 success=False 视为失败。
        if resp.ok and not resp.body.get("success", True):
            return SvResponse(ok=False, status_code=resp.status_code, body=resp.body)
        return resp

    # -- 仿真控制端点（control_signal_inference：启停信号控制算法）------------ #
    def start_simulation(self, config: str) -> SvResponse:
        """``POST /api/simulation/start`` 起信号控制算法仿真（真驱动 SUMO）。

        ANP 过渡期显式请求 SV 的 subprocess 模式：SV 集成模式在部分地图上会秒退，
        subprocess 模式能让运行态被 ``/api/simulation/status`` 稳定观测。
        SV 成功返回 ``{"success": true, "pid": ...}``；已在运行等情形返回 ``success=False``
        （归一为 ``ok=False`` → 执行端回 FAILED）。
        """

        resp = self._post("/api/simulation/start", {"config": config, "execution_mode": "subprocess"})
        if resp.ok and not resp.body.get("success", True):
            return SvResponse(ok=False, status_code=resp.status_code, body=resp.body)
        return resp

    def stop_simulation(self) -> SvResponse:
        """``POST /api/simulation/stop`` 停仿真。无运行中仿真时 SV 返回 ``success=False`` → ``ok=False``。"""

        resp = self._post("/api/simulation/stop", {})
        if resp.ok and not resp.body.get("success", True):
            return SvResponse(ok=False, status_code=resp.status_code, body=resp.body)
        return resp

    def load_map(self, map_path: str) -> SvResponse:
        """``POST /api/load-map {"map_path": <相对 map 目录路径>}`` 切换活动路网（写全局 junction_manager）。

        SV 成功返回 ``{"success": true, "junction_count": ...}``；地图不存在/不支持等 → ``success=False`` → ``ok=False``。
        """

        resp = self._post("/api/load-map", {"map_path": map_path})
        if resp.ok and not resp.body.get("success", True):
            return SvResponse(ok=False, status_code=resp.status_code, body=resp.body)
        return resp
