"""集中视频文本事件库（P7）。

第一版用 SQLite（标准库，零依赖），藏在可替换的 :class:`VideoTextStore` 接口后
（``append`` / ``search`` / ``get``）。后续可换 PostgreSQL / 向量库而不动上层。

存储记录 = envelope 关键字段（``event_id=message_id`` / ``source_agent_id`` /
``event_ts`` / ``confidence`` / ``parent_trace_id``）+ payload 视频特有字段；并保留整条
envelope JSON 以备回放。幂等去重双保险（处理重复投递，AGENTS.md §5.5）：主键
``event_id=message_id`` + 内容级唯一索引 ``(source_agent_id, event_ts, camera_id, text)``。
后者防止上游每次发布换新 ``message_id``（结果桥/重放/多路桥）导致同一逻辑观测重复入库。

``parent_trace_id``（= 回流文本的 ``trace.parent_trace_id`` = 原命令 ``command_id``，P8/P9）
被提升为可检索列，使「协作任务 ↔ 回流文本」可**按 command_id 逐命令归因**检索
（P9 编排器聚合靠此，而非只按内容；见 docs/video.md §10、tasks/task1）。
"""

from __future__ import annotations

import abc
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from anp.contracts import Envelope, EventType, VideoTextEventPayload, parse_iso, parse_payload

from .retrieval import SearchFilters

#: 表名与列（event_ts 存归一化 ISO8601 UTC 带 Z，字符串可直接比较做时间过滤）。
_TABLE = "video_text_events"

#: 摄像头/路口目录表（对齐 wangxuan visionhub `cameras` 轻字段，由 adapter 同步写入）。
#: 与文本事件解耦：位置选择器优先从此目录出（1:1 wangxuan），事件库仍按 _TABLE 检索。
_CAMERAS_TABLE = "video_cameras"


def _norm_ts(ts: str | None) -> str | None:
    """归一化 ISO8601 → UTC 带 Z；便于按字符串比较做时间窗过滤。"""

    if not ts:
        return ts
    try:
        from anp.contracts import iso_utc

        return iso_utc(parse_iso(ts))
    except Exception:  # noqa: BLE001 - 容错：解析失败保持原样
        return ts


def record_from_envelope(env: Envelope) -> dict[str, Any]:
    """把视频文本 envelope 拍平成一条存储记录（不丢字段，原 envelope 也存 JSON）。"""

    if env.event_type != EventType.OBSERVATION_VIDEO_TEXT:
        raise ValueError(f"非视频文本事件，event_type={env.event_type}")
    payload = parse_payload(env)
    assert isinstance(payload, VideoTextEventPayload)  # noqa: S101 - 契约保证
    return {
        "event_id": env.message_id,
        "source_agent_id": env.source.agent_id,
        "event_ts": _norm_ts(env.time.event_ts),
        "confidence": env.quality.confidence,
        #: 回流文本对其源命令的归因键（= 命令 command_id；P8/P9 多 hub 任务聚合按此检索）。
        "parent_trace_id": env.trace.parent_trace_id,
        "camera_id": payload.camera_id,
        "road_name": payload.road_name,
        "intersection_id": payload.intersection_id,
        "road_segment": payload.road_segment,
        "start_ts": _norm_ts(payload.start_ts),
        "end_ts": _norm_ts(payload.end_ts),
        "text": payload.text,
        "summary": payload.summary,
        "category": payload.category,
        "tags": list(payload.tags),
        "entities": dict(payload.entities),
        "artifact_ref": payload.artifact_ref,
        "source_model": payload.source_model,
        "envelope": env.to_wire(),
    }


class VideoTextStore(abc.ABC):
    """视频文本事件库接口（可替换实现）。"""

    @abc.abstractmethod
    def append(self, env: Envelope) -> bool:
        """写入一条文本事件；按 event_id 幂等。返回 True=新写入，False=已存在（去重）。"""

    @abc.abstractmethod
    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        """按时空/关键词过滤检索，按 event_ts 倒序（最近优先）。"""

    @abc.abstractmethod
    def get(self, event_id: str) -> dict[str, Any] | None:
        """按 event_id 取单条。"""

    @abc.abstractmethod
    def count(self) -> int:
        """库内事件总数（冒烟/调试用）。"""

    @abc.abstractmethod
    def distinct_locations(self) -> list[dict[str, Any]]:
        """从库派生「路口 → 摄像头[]」层级（含事件数），供前端位置选择器（task2）。"""

    @abc.abstractmethod
    def browse(self, filters: SearchFilters) -> tuple[list[dict[str, Any]], int]:
        """分页浏览库记录：返回 ``(当前页行, 命中总数)``（数据库视图，task2）。"""


class SqliteVideoTextStore(VideoTextStore):
    """SQLite 实现。每次操作开独立连接，避免多线程共享连接问题。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._write_lock = threading.Lock()
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    event_id        TEXT PRIMARY KEY,
                    source_agent_id TEXT,
                    event_ts        TEXT,
                    confidence      REAL,
                    parent_trace_id TEXT,
                    camera_id       TEXT,
                    road_name       TEXT,
                    intersection_id TEXT,
                    road_segment    TEXT,
                    start_ts        TEXT,
                    end_ts          TEXT,
                    text            TEXT NOT NULL,
                    summary         TEXT,
                    category        TEXT,
                    tags            TEXT,
                    entities        TEXT,
                    artifact_ref    TEXT,
                    source_model    TEXT,
                    envelope        TEXT
                )
                """
            )
            # 迁移：旧库（建表早于 P9）补 parent_trace_id 列，并从已存 envelope JSON 回填。
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()}
            if "parent_trace_id" not in cols:
                conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN parent_trace_id TEXT")
                try:  # json1 通常已编入 sqlite；失败则旧行留 NULL（不影响新写入归因）。
                    conn.execute(
                        f"UPDATE {_TABLE} SET parent_trace_id = "
                        f"json_extract(envelope, '$.trace.parent_trace_id') "
                        f"WHERE parent_trace_id IS NULL AND envelope IS NOT NULL"
                    )
                except sqlite3.OperationalError:
                    pass
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_ts ON {_TABLE}(event_ts)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_road ON {_TABLE}(road_name)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_inter ON {_TABLE}(intersection_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_cam ON {_TABLE}(camera_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_parent ON {_TABLE}(parent_trace_id)")
            # 摄像头/路口目录（对齐 wangxuan `cameras`）。source_id=真身稳定键（主键）。
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_CAMERAS_TABLE} (
                    source_id         INTEGER PRIMARY KEY,
                    camera_id         TEXT,
                    name              TEXT,
                    district          TEXT,
                    intersection_id   TEXT,
                    intersection_name TEXT,
                    primary_road      TEXT,
                    secondary_road    TEXT,
                    camera_position   TEXT,
                    status            TEXT,
                    latitude          REAL,
                    longitude         REAL,
                    synced_at         TEXT
                )
                """
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vc_inter ON {_CAMERAS_TABLE}(intersection_id)")
            # 内容级幂等：同一逻辑观测（来源体+事件时刻+摄像头+文本）只存一条。
            # 仅按 message_id 去重不够——结果桥/重放/多路桥每次发布都新生成 message_id
            # （event_ts 透传不变），会以不同 message_id 重复入库。NULL 安全唯一索引在
            # SQLite 引擎层对所有写入方的 INSERT OR IGNORE 生效（无需各写入方改代码）。
            # 首次迁移：建唯一索引前先清掉历史重复（每组保留最早一行）。
            has_dedup_idx = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_vte_dedup'"
            ).fetchone()
            if not has_dedup_idx:
                conn.execute(
                    f"""DELETE FROM {_TABLE} WHERE rowid NOT IN (
                        SELECT MIN(rowid) FROM {_TABLE}
                        GROUP BY source_agent_id, event_ts, IFNULL(camera_id, ''), text
                    )"""
                )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_vte_dedup "
                f"ON {_TABLE}(source_agent_id, event_ts, IFNULL(camera_id, ''), text)"
            )

    def append(self, env: Envelope) -> bool:
        rec = record_from_envelope(env)
        cols = [
            "event_id", "source_agent_id", "event_ts", "confidence", "parent_trace_id",
            "camera_id", "road_name", "intersection_id", "road_segment", "start_ts", "end_ts",
            "text", "summary", "category", "tags", "entities", "artifact_ref",
            "source_model", "envelope",
        ]
        values = [
            rec["event_id"], rec["source_agent_id"], rec["event_ts"], rec["confidence"],
            rec["parent_trace_id"], rec["camera_id"], rec["road_name"], rec["intersection_id"],
            rec["road_segment"], rec["start_ts"], rec["end_ts"], rec["text"], rec["summary"],
            rec["category"], json.dumps(rec["tags"], ensure_ascii=False),
            json.dumps(rec["entities"], ensure_ascii=False),
            rec["artifact_ref"], rec["source_model"],
            json.dumps(rec["envelope"], ensure_ascii=False),
        ]
        placeholders = ", ".join("?" for _ in cols)
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            return cur.rowcount > 0

    @staticmethod
    def _where(filters: SearchFilters) -> tuple[str, list[Any]]:
        """把过滤条件编译成 ``(WHERE 子句, params)``，供 search/browse/count 共用。

        子句不含前导 ``WHERE``（无条件时返回空串）；调用方按需拼接。
        """

        where: list[str] = []
        params: list[Any] = []
        if filters.time_from:
            where.append("event_ts >= ?")
            params.append(_norm_ts(filters.time_from))
        if filters.time_to:
            where.append("event_ts <= ?")
            params.append(_norm_ts(filters.time_to))
        if filters.road_name:
            where.append("(road_name LIKE ? OR road_segment LIKE ?)")
            like = f"%{filters.road_name}%"
            params.extend([like, like])
        if filters.intersection_id:
            where.append("intersection_id = ?")
            params.append(filters.intersection_id)
        if filters.camera_id:
            where.append("camera_id = ?")
            params.append(filters.camera_id)
        # 命令归因：按回流文本的 parent_trace_id（= 命令 command_id）精确召回（P9 任务聚合）。
        ptids = [p for p in (filters.parent_trace_ids or []) if p]
        if ptids:
            placeholders = ", ".join("?" for _ in ptids)
            where.append(f"parent_trace_id IN ({placeholders})")
            params.extend(ptids)
        if filters.category:
            where.append("category LIKE ?")
            params.append(f"%{filters.category}%")
        # 关键词：OR 匹配 text/summary/category/road_name；多关键词彼此 OR。
        kw_terms = [k for k in (filters.keywords or []) if k]
        if kw_terms:
            kw_clauses = []
            for kw in kw_terms:
                kw_clauses.append("(text LIKE ? OR summary LIKE ? OR category LIKE ? OR road_name LIKE ?)")
                like = f"%{kw}%"
                params.extend([like, like, like, like])
            where.append("(" + " OR ".join(kw_clauses) + ")")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return clause, params

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
        clause, params = self._where(filters)
        sql = f"SELECT * FROM {_TABLE}{clause} ORDER BY event_ts DESC LIMIT ?"
        params.append(max(1, int(filters.limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def browse(self, filters: SearchFilters) -> tuple[list[dict[str, Any]], int]:
        """分页浏览（数据库视图，task2）：同一 WHERE 下取当前页行 + 命中总数。"""

        clause, params = self._where(filters)
        limit = max(1, int(filters.limit))
        offset = max(0, int(filters.offset))
        page_sql = f"SELECT * FROM {_TABLE}{clause} ORDER BY event_ts DESC LIMIT ? OFFSET ?"
        count_sql = f"SELECT COUNT(*) FROM {_TABLE}{clause}"
        with self._connect() as conn:
            total = int(conn.execute(count_sql, params).fetchone()[0])
            rows = conn.execute(page_sql, [*params, limit, offset]).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def distinct_locations(self) -> list[dict[str, Any]]:
        """派生「路口 → 摄像头[]」层级（含事件数），供前端位置选择器（task2）。

        分组键：优先 ``intersection_id``；为 null 时回退按 ``road_name`` 兜底（合成
        key ``road:<名>``），都为 null 归 ``__ungrouped__``。每路口聚合事件总数与
        ``road_name``（取该组首个非空），其下摄像头按事件数倒序、camera_id 升序排列。
        路口按事件数倒序返回（最活跃在前），同数按合成 key 稳定排序。
        """

        sql = (
            f"SELECT intersection_id, road_name, camera_id, COUNT(*) AS n "
            f"FROM {_TABLE} GROUP BY intersection_id, road_name, camera_id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()

        groups: dict[str, dict[str, Any]] = {}
        for r in rows:
            inter = r["intersection_id"]
            road = r["road_name"]
            if inter:
                key = inter
            elif road:
                key = f"road:{road}"
            else:
                key = "__ungrouped__"
            grp = groups.setdefault(
                key,
                {
                    "key": key,
                    "intersection_id": inter,
                    "road_name": road,
                    "event_count": 0,
                    "cameras": {},
                },
            )
            grp["event_count"] += int(r["n"])
            if not grp["road_name"] and road:
                grp["road_name"] = road
            cam = r["camera_id"] or ""
            grp["cameras"][cam] = grp["cameras"].get(cam, 0) + int(r["n"])

        result: list[dict[str, Any]] = []
        for grp in groups.values():
            cameras = [
                {"camera_id": cam, "event_count": cnt}
                for cam, cnt in sorted(
                    grp["cameras"].items(), key=lambda kv: (-kv[1], kv[0])
                )
                if cam  # 跳过无 camera_id 的占位（理论上视频事件必有 camera_id）
            ]
            result.append(
                {
                    "intersection_id": grp["intersection_id"],
                    "road_name": grp["road_name"],
                    "event_count": grp["event_count"],
                    "cameras": cameras,
                }
            )
        result.sort(key=lambda g: (-g["event_count"], g["intersection_id"] or g["road_name"] or ""))
        return result

    # -- 摄像头/路口目录（对齐 wangxuan visionhub `cameras`）------------------ #
    def replace_cameras(self, rows: list[dict[str, Any]]) -> int:
        """全量替换摄像头目录（一次性同步快照语义）。返回写入条数。

        ``rows`` 为已映射成 ANP 原生形态的记录（键见 :data:`_CAMERAS_TABLE` 列）。
        全量 DELETE+INSERT，保证目录与真身 `cameras` 表 1:1（含删除已下线源）。
        """

        cols = (
            "source_id", "camera_id", "name", "district", "intersection_id",
            "intersection_name", "primary_road", "secondary_road", "camera_position",
            "status", "latitude", "longitude", "synced_at",
        )
        placeholders = ", ".join("?" for _ in cols)
        with self._write_lock, self._connect() as conn:
            conn.execute(f"DELETE FROM {_CAMERAS_TABLE}")
            conn.executemany(
                f"INSERT OR REPLACE INTO {_CAMERAS_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                [tuple(r.get(c) for c in cols) for r in rows],
            )
            conn.commit()
        return len(rows)

    def camera_count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {_CAMERAS_TABLE}").fetchone()[0])

    def get_camera(self, source_id: int) -> dict[str, Any] | None:
        """按真身稳定键 ``source_id`` 取单条目录摄像头（CLI 解析真身标识用）。"""

        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {_CAMERAS_TABLE} WHERE source_id = ?", (int(source_id),)
            ).fetchone()
        return dict(row) if row else None

    def camera_source_index(self) -> dict[int, dict[str, Any]]:
        """``source_id`` → ``{camera_id, intersection_id, road_name}``（events 回填用）。

        按 source 把真身历史事件对齐到已同步的目录（road_name 取 primary_road，缺则
        intersection_name）。供 :func:`anp.adapters.visionhub.catalog.map_event` 填对齐键。
        """

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT source_id, camera_id, intersection_id, primary_road, intersection_name "
                f"FROM {_CAMERAS_TABLE}"
            ).fetchall()
        return {
            int(r["source_id"]): {
                "camera_id": r["camera_id"],
                "intersection_id": r["intersection_id"],
                "road_name": r["primary_road"] or r["intersection_name"],
            }
            for r in rows if r["source_id"] is not None
        }

    def catalog_locations(self) -> list[dict[str, Any]]:
        """从摄像头目录派生「路口 → 摄像头[]」层级（对齐 wangxuan，1:1）。

        分组键：``intersection_id``（由 intersection_name 派生）；为空归 ``__ungrouped__``
        （wangxuan 无路口的孤儿源）。每摄像头是一个 source（全量 201）。

        **事件计数（step2 对齐）**：回流文本事件挂到目录的两级粒度——
        - **相机级** ``event_count`` 按 ``camera_id`` 精确命中（目录 camera_id 唯一，且整链
          忠实透传真身 camera_id，故等价于按 source 归属）；
        - **路口级** ``event_count`` 按 ``intersection_id`` 归属：事件自带的 ``intersection_id``
          命中目录路口即计入；若事件无 intersection_id 但其 ``camera_id`` 属某目录相机，则经该
          相机回溯到其路口计入（每事件只计一次）。这样「所有摄像头」类事件（camera_id 退化为
          ``unknown-camera`` 但带真 intersection_id）也能挂在正确路口下，而不再恒 0。
        路口按摄像头数倒序、孤儿组置末。
        """

        with self._connect() as conn:
            cam_rows = conn.execute(
                f"SELECT source_id, camera_id, name, district, intersection_id, "
                f"intersection_name, primary_road, secondary_road, camera_position, status "
                f"FROM {_CAMERAS_TABLE}"
            ).fetchall()
            # 事件按 (camera_id, intersection_id) 分组一次，下面同时派生相机级/路口级计数。
            ev_rows = conn.execute(
                f"SELECT camera_id, intersection_id, COUNT(*) AS n FROM {_TABLE} GROUP BY camera_id, intersection_id"
            ).fetchall()

        # 目录侧索引：camera_id→所属路口、目录已知的路口 id 集合。
        cam_to_inter: dict[str, str | None] = {}
        catalog_inter_ids: set[str] = set()
        for r in cam_rows:
            if r["camera_id"] is not None:
                cam_to_inter[r["camera_id"]] = r["intersection_id"]
            if r["intersection_id"]:
                catalog_inter_ids.add(r["intersection_id"])

        cam_ev: dict[str, int] = {}          # camera_id → 事件数（相机级）
        inter_ev: dict[str, int] = {}        # 目录 intersection_id → 事件数（路口级，每事件计一次）
        for er in ev_rows:
            cam, inter, n = er["camera_id"], er["intersection_id"], int(er["n"])
            if cam is not None:
                cam_ev[cam] = cam_ev.get(cam, 0) + n
            # 路口归属：优先事件自带的 intersection_id（命中目录），否则经相机回溯。
            if inter in catalog_inter_ids:
                target = inter
            else:
                target = cam_to_inter.get(cam) if cam is not None else None
            if target:
                inter_ev[target] = inter_ev.get(target, 0) + n

        groups: dict[str, dict[str, Any]] = {}
        for r in cam_rows:
            inter_id = r["intersection_id"] or "__ungrouped__"
            grp = groups.setdefault(
                inter_id,
                {
                    "intersection_id": r["intersection_id"],
                    "intersection_name": r["intersection_name"],
                    "road_name": r["primary_road"],
                    "district": r["district"],
                    "event_count": 0,
                    "cameras": [],
                },
            )
            # 路口级元数据取该组首个非空。
            for k_grp, k_row in (("intersection_name", "intersection_name"),
                                 ("road_name", "primary_road"), ("district", "district")):
                if not grp[k_grp] and r[k_row]:
                    grp[k_grp] = r[k_row]
            grp["cameras"].append(
                {
                    "source_id": r["source_id"],
                    "camera_id": r["camera_id"],
                    "name": r["name"],
                    "camera_position": r["camera_position"],
                    "event_count": cam_ev.get(r["camera_id"], 0),
                }
            )

        for inter_id, grp in groups.items():
            if grp["intersection_id"]:
                # 路口级按 intersection_id 归属（含经相机回溯的事件）。
                grp["event_count"] = inter_ev.get(grp["intersection_id"], 0)
            else:
                # 孤儿组无路口键，回退按其相机命中之和。
                grp["event_count"] = sum(c["event_count"] for c in grp["cameras"])

        result = list(groups.values())
        for grp in result:
            # 摄像头按方位、再按 source_id 稳定排序。
            grp["cameras"].sort(key=lambda c: (c["camera_position"] or "~", c["source_id"] or 0))
        # 路口按摄像头数倒序；孤儿组（intersection_id is None）置末。
        result.sort(key=lambda g: (g["intersection_id"] is None, -len(g["cameras"]),
                                   g["intersection_name"] or ""))
        return result

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE event_id = ?", (event_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for json_col in ("tags", "entities", "envelope"):
            if d.get(json_col):
                try:
                    d[json_col] = json.loads(d[json_col])
                except (TypeError, json.JSONDecodeError):
                    pass
        return d
