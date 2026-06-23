"""协作视频任务：Task 抽象 + 任务存储（P9）。

一个「协作视频任务」= { 目标 prompt、范围 scope（路段/摄像头/目标 hub 集合/时间窗）、
扇出的 N 条命令（各 ``command_id`` + ``target_agent_id`` + 状态）、整体状态、聚合答案 }。
编排器（:mod:`anp.video.orchestrator`）扇出定向命令、按 ``command_id`` 归因回流文本、
用 :class:`VideoQAService` 聚合成带证据的答案，状态落到本模块的任务存储。

Task 是**编排/存储态**，不是 Kafka wire 消息——故放 video 域而非 contracts（wire 契约
envelope/command/``request_video_text`` 已存在并复用）。存储用 SQLite（与文本库同目录
``backend/.data/``，独立文件 ``video_tasks.db``），藏在可替换接口后。
"""

from __future__ import annotations

import abc
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from anp.contracts import now_iso

#: 单条命令在任务内的状态机：已发→（回流）已返回 / 失败。
CommandStatus = Literal["pending", "dispatched", "returned", "failed"]
#: 任务整体状态：创建→运行（已扇出，等回流）→已聚合 / 失败。
TaskStatus = Literal["pending", "running", "aggregated", "failed"]

_TABLE = "video_tasks"


class TaskScope(BaseModel):
    """任务范围：按路段/摄像头发起，可显式指定目标 hub 集合与时间窗。"""

    model_config = ConfigDict(extra="forbid")

    road_name: str | None = None
    camera_id: str | None = None
    intersection_id: str | None = None
    road_segment: str | None = None
    time_from: str | None = None  # ISO8601 UTC
    time_to: str | None = None
    #: 显式目标 vision hub 体集合；留空则由编排器按 roster/registry 选（MVP 默认替身桩）。
    target_agent_ids: list[str] = Field(default_factory=list)


class TaskCommand(BaseModel):
    """任务扇出的一条定向命令的归因记录（command_id ⇄ 回流文本）。"""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    target_agent_id: str
    status: CommandStatus = "dispatched"
    returned_event_id: str | None = None
    returned_ts: str | None = None


class VideoTask(BaseModel):
    """一个协作视频任务的完整状态。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    module: str  # 命令模块 key（MVP: request_video_text）
    prompt: str
    scope: TaskScope
    commands: list[TaskCommand] = Field(default_factory=list)
    status: TaskStatus = "pending"
    answer: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def returned_count(self) -> int:
        return sum(1 for c in self.commands if c.status == "returned")


class VideoTaskStore(abc.ABC):
    """任务存储接口（可替换实现）。"""

    @abc.abstractmethod
    def upsert(self, task: VideoTask) -> None: ...

    @abc.abstractmethod
    def get(self, task_id: str) -> VideoTask | None: ...

    @abc.abstractmethod
    def list(self, limit: int = 50) -> list[VideoTask]: ...

    @abc.abstractmethod
    def count(self) -> int: ...


class SqliteVideoTaskStore(VideoTaskStore):
    """SQLite 实现：整条任务存 JSON，另抽 task_id/status/created_at 列供列表/排序。"""

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
                    task_id    TEXT PRIMARY KEY,
                    status     TEXT,
                    module     TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    task       TEXT NOT NULL
                )
                """
            )
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vt_created ON {_TABLE}(created_at)")

    def upsert(self, task: VideoTask) -> None:
        payload = task.model_dump_json()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                f"""INSERT INTO {_TABLE} (task_id, status, module, created_at, updated_at, task)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        status=excluded.status, module=excluded.module,
                        updated_at=excluded.updated_at, task=excluded.task""",
                (task.task_id, task.status, task.module, task.created_at, task.updated_at, payload),
            )

    def get(self, task_id: str) -> VideoTask | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT task FROM {_TABLE} WHERE task_id = ?", (task_id,)
            ).fetchone()
        return VideoTask.model_validate_json(row["task"]) if row else None

    def list(self, limit: int = 50) -> list[VideoTask]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT task FROM {_TABLE} ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [VideoTask.model_validate_json(r["task"]) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0])
