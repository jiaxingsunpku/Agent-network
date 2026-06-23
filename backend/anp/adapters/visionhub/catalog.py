"""wangxuan visionhub 摄像头/路口目录同步（轻数据对齐 step1）。

只读真身 PostgreSQL `cameras` 表的**轻字段**（路口/道路/方位/状态/经纬度——**绝不含视频、
帧、轨迹**），经 ssh + psql 拉成 JSON → 映射成 ANP 原生目录记录，由调用方写入 ANP
`video_cameras` 表。**懂真身 PG schema 的耦合只在本 adapter 内**（镜像 SignalVision/桥的
「只在 adapter 内懂外部原生结构」原则）。

设计要点：
- ``source_id`` 是真身稳定键（整数，201 唯一；``camera_id`` 是带时间戳的脏串，仅作标签）。
- 真身 `cameras` 无干净路口键 → ``intersection_id`` 由 ``intersection_name`` 派生（确定性 ascii）。
- 不直连 PG（不引 psycopg/不开新隧道）：psql 在 wangxuan 本机跑、连其 localhost:5432。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any
from urllib.parse import urlparse

#: 真身 `cameras` 表要拉的轻字段白名单（**绝不含**视频/帧/轨迹/检测框）。
CAMERA_COLUMNS: tuple[str, ...] = (
    "source_id", "camera_id", "name", "district", "intersection_name",
    "primary_road", "secondary_road", "camera_position", "status",
    "latitude", "longitude",
)

#: 真身 `events` 表要拉的轻字段白名单（文本/类别/时空/source 关联 + 轻元数据；**绝不含 bbox/帧**）。
EVENT_COLUMNS: tuple[str, ...] = (
    "id", "source_id", "event_type", "severity", "description",
    "detected_at", "confidence", "track_ids",
)

#: 真身 `events.event_type` → ANP 文本事件 ``category`` 的映射（其余原样透传）。
CATEGORY_BY_EVENT_TYPE: dict[str, str] = {
    "speeding_vehicle": "超速",
    "traffic_congestion": "拥堵",
}

#: 历史事件回填的来源体身份（溯源用，区别于 live VLM 回流 ``video-perception-visionhub-001``）。
VISIONHUB_EVENTS_AGENT_ID = "video-perception-visionhub-events-001"


def derive_intersection_id(name: str | None) -> str | None:
    """从路口名派生稳定 ascii ``intersection_id``（真身 cameras 无干净路口键）。

    确定性：同名永远同 id；``vh-`` 前缀与 ANP 自有 ``gg-xiongchu-minzu`` 等区分。
    """

    name = (name or "").strip()
    if not name:
        return None
    return "vh-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def map_camera(row: dict[str, Any], synced_at: str) -> dict[str, Any]:
    """真身 cameras 行 → ANP ``video_cameras`` 记录（纯函数，可单测）。"""

    inter_name = _clean(row.get("intersection_name"))
    return {
        "source_id": row.get("source_id"),
        "camera_id": _clean(row.get("camera_id")),
        "name": _clean(row.get("name")),
        "district": _clean(row.get("district")),
        "intersection_id": derive_intersection_id(inter_name),
        "intersection_name": inter_name,
        "primary_road": _clean(row.get("primary_road")),
        "secondary_road": _clean(row.get("secondary_road")),
        "camera_position": _clean(row.get("camera_position")),
        "status": _clean(row.get("status")),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "synced_at": synced_at,
    }


def _psql_json_query() -> str:
    cols = ", ".join(CAMERA_COLUMNS)
    # COALESCE 保证空表也返回 '[]' 而非空串。
    return f"SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) FROM (SELECT {cols} FROM cameras) t"


def fetch_visionhub_cameras(ssh_host: str, dsn: str, *, timeout: float = 60.0) -> list[dict[str, Any]]:
    """ssh 到 ``ssh_host`` 跑 psql，把真身 cameras 轻字段拉成 ``list[dict]``。

    ``dsn`` = ``postgresql://user:pass@host:port/db``（从 ``backend/.env`` 读，不入库）。
    psql 在 wangxuan 本机执行、连其 localhost PG（不开跨机 PG 隧道）。
    """

    u = urlparse(dsn)
    pw = u.password or ""
    user = u.username or "postgres"
    host = u.hostname or "127.0.0.1"
    port = str(u.port or 5432)
    db = (u.path or "/").lstrip("/") or "postgres"
    remote = (
        f"PGPASSWORD={pw} psql -h {host} -p {port} -U {user} -d {db} "
        f'-At -c "{_psql_json_query()}"'
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host, remote],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh+psql 拉 cameras 失败 (rc={proc.returncode}): {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    return json.loads(out) if out else []


# --------------------------------------------------------------------------- #
# events 轻数据同步（对齐 step3：真身历史事件 → ANP 文本事件库）
# --------------------------------------------------------------------------- #
def _events_psql_json_query(limit: int | None, event_types: tuple[str, ...] | None) -> str:
    cols = ", ".join(EVENT_COLUMNS)
    where = ""
    if event_types:
        # event_types 来自固定白名单（CATEGORY_BY_EVENT_TYPE 键），单引号转义防注入。
        vals = ",".join("'" + t.replace("'", "''") + "'" for t in event_types)
        where = f" WHERE event_type IN ({vals})"
    tail = " ORDER BY detected_at"
    if limit:
        tail += f" LIMIT {int(limit)}"
    return (
        f"SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) "
        f"FROM (SELECT {cols} FROM events{where}{tail}) t"
    )


def fetch_visionhub_events(
    ssh_host: str,
    dsn: str,
    *,
    limit: int | None = None,
    event_types: tuple[str, ...] | None = None,
    timeout: float = 180.0,
) -> list[dict[str, Any]]:
    """ssh 到 ``ssh_host`` 跑 psql，把真身 events 轻字段拉成 ``list[dict]``（镜像 cameras 拉取）。

    只读 :data:`EVENT_COLUMNS`（文本/类别/时空/source 关联），**绝不含 bbox/帧**。
    ``limit`` 限条数（按 detected_at 升序，便于抽样），``event_types`` 过滤事件类型。
    """

    u = urlparse(dsn)
    pw = u.password or ""
    user = u.username or "postgres"
    host = u.hostname or "127.0.0.1"
    port = str(u.port or 5432)
    db = (u.path or "/").lstrip("/") or "postgres"
    remote = (
        f"PGPASSWORD={pw} psql -h {host} -p {port} -U {user} -d {db} "
        f'-At -c "{_events_psql_json_query(limit, event_types)}"'
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_host, remote],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ssh+psql 拉 events 失败 (rc={proc.returncode}): {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    return json.loads(out) if out else []


def map_event(row: dict[str, Any], source_map: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """真身 events 行 → ANP 文本事件 dict（:class:`VideoTextEventIn` 兼容；纯函数，可单测）。

    经 ``source_id`` 命中 ``source_map``（ANP 已同步的目录，source_id→camera_id/intersection_id/
    road_name）填充对齐键，使回填事件挂到正确相机/路口（step2 连接生效）。``message_id`` 取真身
    event id 派生 ``vh-evt-<id>``，使重复同步幂等（store 主键去重）。
    """

    sid = row.get("source_id")
    cam = source_map.get(sid) if sid is not None else None
    camera_id = (cam or {}).get("camera_id") or (f"vh-source-{sid}" if sid is not None else "unknown-camera")
    road_name = (cam or {}).get("road_name")
    intersection_id = (cam or {}).get("intersection_id")

    event_type = _clean(row.get("event_type"))
    category = CATEGORY_BY_EVENT_TYPE.get(event_type or "", event_type)
    severity = _clean(row.get("severity"))
    desc = _clean(row.get("description")) or f"{category or '视频'}事件（severity={severity or '未知'}）"
    track_ids = row.get("track_ids")
    conf = row.get("confidence")
    confidence = conf if isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0.0 <= conf <= 1.0 else None

    return {
        "message_id": f"vh-evt-{row.get('id')}",
        "camera_id": str(camera_id),
        "road_name": road_name,
        "intersection_id": intersection_id,
        "text": str(desc),
        "summary": f"{road_name or '未知路段'}{category}" if category else (road_name or "视频事件"),
        "category": category,
        "tags": [t for t in [category, severity] if t],
        "entities": {
            "source_event_id": row.get("id"),
            "event_type": event_type,
            "severity": severity,
            "track_ids": list(track_ids) if isinstance(track_ids, list) else [],
        },
        "source_model": "visionhub-events",
        "event_ts": _clean(row.get("detected_at")),
        "confidence": confidence,
        "source_agent_id": VISIONHUB_EVENTS_AGENT_ID,
    }
