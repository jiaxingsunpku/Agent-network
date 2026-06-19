"""集中视频文本事件库（P7）。

第一版用 SQLite（标准库，零依赖），藏在可替换的 :class:`VideoTextStore` 接口后
（``append`` / ``search`` / ``get``）。后续可换 PostgreSQL / 向量库而不动上层。

存储记录 = envelope 关键字段（``event_id=message_id`` / ``source_agent_id`` /
``event_ts`` / ``confidence``）+ payload 视频特有字段；并保留整条 envelope JSON 以备回放。
按 ``message_id`` 幂等去重，处理重复投递（AGENTS.md §5.5）。
"""

from __future__ import annotations

import abc
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from anp.contracts import Envelope, EventType, VideoTextEventPayload, parse_iso, parse_payload

from .retrieval import SearchFilters

#: 表名与列（event_ts 存归一化 ISO8601 UTC 带 Z，字符串可直接比较做时间过滤）。
_TABLE = "video_text_events"


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


class SqliteVideoTextStore(VideoTextStore):
    """SQLite 实现。每次操作开独立连接，避免多线程共享连接问题。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._write_lock = threading.Lock()
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    event_id        TEXT PRIMARY KEY,
                    source_agent_id TEXT,
                    event_ts        TEXT,
                    confidence      REAL,
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
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_ts ON {_TABLE}(event_ts)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_road ON {_TABLE}(road_name)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_inter ON {_TABLE}(intersection_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vte_cam ON {_TABLE}(camera_id)")

    def append(self, env: Envelope) -> bool:
        rec = record_from_envelope(env)
        cols = [
            "event_id", "source_agent_id", "event_ts", "confidence", "camera_id",
            "road_name", "intersection_id", "road_segment", "start_ts", "end_ts",
            "text", "summary", "category", "tags", "entities", "artifact_ref",
            "source_model", "envelope",
        ]
        values = [
            rec["event_id"], rec["source_agent_id"], rec["event_ts"], rec["confidence"],
            rec["camera_id"], rec["road_name"], rec["intersection_id"], rec["road_segment"],
            rec["start_ts"], rec["end_ts"], rec["text"], rec["summary"], rec["category"],
            json.dumps(rec["tags"], ensure_ascii=False), json.dumps(rec["entities"], ensure_ascii=False),
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

    def search(self, filters: SearchFilters) -> list[dict[str, Any]]:
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
        sql = f"SELECT * FROM {_TABLE}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY event_ts DESC LIMIT ?"
        params.append(max(1, int(filters.limit)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

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
