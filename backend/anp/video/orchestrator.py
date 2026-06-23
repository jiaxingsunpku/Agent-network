"""协作视频任务编排器（P9）——薄编排：扇出定向命令 → 收集回流 → QA 聚合。

ANP 作系统级编排者：对一个路段/摄像头发起协作视频任务，从 roster/registry 选目标 vision hub
体集合，**逐个下发定向命令**（每条带唯一 ``command_id`` 与 ``target_agent_id``，**禁 broadcast**，
AGENTS §3.5），命令直发 ``anp.video.command.v1`` 控制层。回流文本经 P7 ingest 入库后，按
``command_id``⇄``parent_trace_id`` 逐命令归因（P8），命中后用 :class:`VideoQAService` 聚合成
带证据的答案（A1：复用 QA，不另造 LLM 调用）。

边界（tasks/task1/readme §4）：
- **不在网关算聚合**（AGENTS §3.4）——聚合在本编排器（任务体侧）。
- 视频命令走 ``VideoTopics.COMMAND``（≠ 交通 ``TrafficTopics.COMMAND``），故直发、不复用网关交通
  ``/commands`` 管道（否则发错 topic）；但仍守其校验纪律：``target_agent_id`` 必填、禁 broadcast、
  命令类型走契约白名单（``CommandType``）。
- 异步黑板模型：``create_task`` 只扇出、不阻塞等结果；``refresh_task`` 按需收集 + 聚合。
"""

from __future__ import annotations

from typing import Any

from anp.adapters.visionhub.config import VISIONHUB_AGENT_ID
from anp.contracts import (
    CommandPayload,
    Envelope,
    Source,
    SourceSystem,
    VideoTopics,
    command_envelope,
    expires_at_iso,
    new_message_id,
    now_iso,
)
from anp.messaging import make_producer, publish

from .command_modules import REQUEST_VIDEO_TEXT, get_command_module
from .config import VIDEO_TASK_AGENT_ID
from .qa import VideoQAService
from .retrieval import SearchFilters
from .store import VideoTextStore
from .tasks import TaskCommand, TaskScope, VideoTask, VideoTaskStore

#: MVP 默认 vision hub roster（替身桩扮演的远端推理体；scope 未显式指定目标时用）。
#: 真·多 hub 由 ``scope.target_agent_ids`` 显式给出，或后续从 registry 按 capability 选。
DEFAULT_VISIONHUB_ROSTER: tuple[str, ...] = (VISIONHUB_AGENT_ID,)

#: envelope 上严禁出现的群发字段（防御性断言，AGENTS §3.5）。
_FORBIDDEN_WIRE_KEYS = ("broadcast", "agent_ids")


class CommandModuleError(ValueError):
    """命令模块非法 / 未落地执行端（→ HTTP 400）。"""


class NoTargetsError(ValueError):
    """无可用目标 vision hub 体（→ HTTP 400）。"""


class PublishUnavailable(RuntimeError):
    """无可用 Kafka producer（→ HTTP 503）。"""


class VideoTaskOrchestrator:
    """协作视频任务编排器（扇出 + 聚合）。"""

    def __init__(
        self,
        *,
        task_store: VideoTaskStore,
        text_store: VideoTextStore,
        qa: VideoQAService,
        producer=None,
        bootstrap: str | None = None,
        roster: tuple[str, ...] | list[str] = DEFAULT_VISIONHUB_ROSTER,
        source_agent_id: str = VIDEO_TASK_AGENT_ID,
        command_topic: str = VideoTopics.COMMAND,
        expires_sec: float = 300.0,
        aggregate_limit: int = 50,
    ) -> None:
        self.task_store = task_store
        self.text_store = text_store
        self.qa = qa
        self._producer = producer
        self._bootstrap = bootstrap
        self._owns_producer = False
        self.roster = tuple(roster)
        self.source_agent_id = source_agent_id
        self.command_topic = command_topic
        self.expires_sec = expires_sec
        self.aggregate_limit = aggregate_limit

    # -- producer（注入优先；否则按 bootstrap 懒构造）---------------------- #
    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            self._producer = make_producer(bootstrap_servers=self._bootstrap)
            self._owns_producer = True
        except Exception as exc:  # noqa: BLE001
            raise PublishUnavailable(f"无法连接 Kafka 发布视频命令：{exc}") from exc
        return self._producer

    def close(self) -> None:
        if self._owns_producer and self._producer is not None:
            try:
                self._producer.flush()
                self._producer.close()
            finally:
                self._producer = None
                self._owns_producer = False

    # -- 目标体筛选 ------------------------------------------------------- #
    def select_targets(self, scope: TaskScope, module: str) -> list[str]:
        """选目标 vision hub 体集合：显式 ``target_agent_ids`` 优先，否则用 roster。

        保序去重；为空抛 :class:`NoTargetsError`。设计上支持 N>1（逐个定向扇出）。
        """

        ids = list(scope.target_agent_ids) if scope.target_agent_ids else list(self.roster)
        seen: set[str] = set()
        targets = [i for i in ids if i and not (i in seen or seen.add(i))]
        if not targets:
            raise NoTargetsError("无可用目标 vision hub 体（roster 为空且未显式指定）")
        return targets

    # -- 命令参数（按 task scope 填充 request_video_text）----------------- #
    @staticmethod
    def _build_params(scope: TaskScope, prompt: str) -> dict[str, Any]:
        time_window = None
        if scope.time_from or scope.time_to:
            time_window = {"time_from": scope.time_from, "time_to": scope.time_to}
        return {
            "camera_id": scope.camera_id,
            "road_name": scope.road_name,
            "intersection_id": scope.intersection_id,
            "road_segment": scope.road_segment,
            "time_window": time_window,
            "prompt": prompt,
            "clip_ref": None,
        }

    def _build_command(self, *, target_agent_id: str, command_id: str, command_type, scope, prompt) -> Envelope:
        env = command_envelope(
            source=Source(system=SourceSystem.PLATFORM, agent_id=self.source_agent_id),
            target_agent_id=target_agent_id,
            payload=CommandPayload(command_id=command_id, command_type=command_type, params=self._build_params(scope, prompt)),
            expires_at=expires_at_iso(self.expires_sec),
            object_id=scope.intersection_id or scope.road_name or scope.camera_id,
        )
        # 防御性：定向命令必带单一 target、绝不出现群发字段（AGENTS §3.5）。
        assert env.target.agent_id == target_agent_id, "命令缺失 target_agent_id"
        wire = env.to_wire()
        for key in _FORBIDDEN_WIRE_KEYS:
            assert key not in wire and key not in wire.get("target", {}), f"命令出现禁用群发字段 {key}"
        return env

    # -- 扇出：建任务 + 逐条定向命令 ------------------------------------- #
    def create_task(self, prompt: str, scope: TaskScope, *, module: str = REQUEST_VIDEO_TEXT) -> VideoTask:
        """对一个范围发起协作视频任务：扇出 N 条定向命令并落任务（status=running）。"""

        if not prompt or not prompt.strip():
            raise CommandModuleError("prompt 不能为空")
        mod = get_command_module(module)
        if mod is None:
            raise CommandModuleError(f"未知命令模块：{module}")
        if not mod.implemented or mod.command_type is None:
            raise CommandModuleError(f"命令模块 {module} 未落地执行端（vision hub 职责，本期占位）")

        targets = self.select_targets(scope, module)
        producer = self._get_producer()

        task_id = new_message_id()
        commands: list[TaskCommand] = []
        for hub in targets:
            command_id = new_message_id()
            env = self._build_command(
                target_agent_id=hub, command_id=command_id, command_type=mod.command_type, scope=scope, prompt=prompt
            )
            publish(producer, self.command_topic, env, flush=True)
            commands.append(TaskCommand(command_id=command_id, target_agent_id=hub, status="dispatched"))

        task = VideoTask(
            task_id=task_id, module=module, prompt=prompt, scope=scope, commands=commands, status="running"
        )
        self.task_store.upsert(task)
        return task

    @staticmethod
    def _command_ids(task: VideoTask) -> list[str]:
        return [c.command_id for c in task.commands if c.command_id]

    def _status_hits(self, command_ids: list[str]) -> list[dict[str, Any]]:
        if not command_ids:
            return []
        return self.text_store.search(
            SearchFilters(parent_trace_ids=command_ids, limit=max(self.aggregate_limit, len(command_ids) * 2))
        )

    @staticmethod
    def _latest_by_command(hits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        by_cmd: dict[str, dict[str, Any]] = {}
        for h in hits:  # store 按 event_ts DESC；每命令取最近一条
            pid = h.get("parent_trace_id")
            if pid and pid not in by_cmd:
                by_cmd[pid] = h
        return by_cmd

    @staticmethod
    def _hits_for_task(task: VideoTask, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cmd_ids = {c.command_id for c in task.commands}
        return [h for h in hits if h.get("parent_trace_id") in cmd_ids]

    def _result_filters(self, task: VideoTask, command_ids: list[str]) -> SearchFilters:
        return SearchFilters(
            parent_trace_ids=command_ids,
            road_name=task.scope.road_name,
            camera_id=task.scope.camera_id,
            intersection_id=task.scope.intersection_id,
            time_from=task.scope.time_from,
            time_to=task.scope.time_to,
            limit=self.aggregate_limit,
        )

    def _apply_hits(
        self,
        task: VideoTask,
        hits: list[dict[str, Any]],
        *,
        aggregate: bool,
        use_llm: bool | None,
    ) -> bool:
        """把本地回流事件应用到任务态。返回是否有状态/答案变化。"""

        changed = False
        by_cmd = self._latest_by_command(hits)
        for c in task.commands:
            ev = by_cmd.get(c.command_id)
            if not ev:
                continue
            returned_event_id = ev.get("event_id")
            returned_ts = ev.get("event_ts")
            if c.status != "returned" or c.returned_event_id != returned_event_id or c.returned_ts != returned_ts:
                c.status = "returned"
                c.returned_event_id = returned_event_id
                c.returned_ts = returned_ts
                changed = True

        returned = task.returned_count
        all_returned = bool(task.commands) and returned == len(task.commands)
        next_status = "aggregated" if all_returned else "running" if returned else task.status
        if task.status != next_status:
            task.status = next_status
            changed = True

        # 本地快路径：全部回流后先用已入库事件生成/缓存规则摘要，避免详情点击阻塞外部 LLM。
        should_summarize = aggregate and returned and (use_llm is True or not task.answer)
        if should_summarize:
            cmd_ids = self._command_ids(task)
            result = self.qa.answer_from_records(
                task.prompt,
                hits[: self.aggregate_limit],
                filters=self._result_filters(task, cmd_ids),
                use_llm=use_llm,
            )
            task.answer = result["answer"]
            task.evidence = result["evidence"]
            task.warnings = result["warnings"]
            changed = True

        return changed

    # -- 收集 + 聚合：按 command_id 归因回流文本，QA 合成 ----------------- #
    def refresh_task(
        self,
        task_id: str,
        *,
        aggregate: bool = True,
        use_llm: bool | None = None,
    ) -> VideoTask | None:
        """刷新任务：按 ``parent_trace_id``==``command_id`` 归因回流文本、更新各命令状态；
        若 ``aggregate`` 且有回流，则用 QA 聚合成带证据答案、整体状态→aggregated。

        归因只用 ``command_id``（精确，R2/R3「勿只按内容」）。聚合 ``extract=False`` 防止
        启发式过滤误删归因命中。本方法只读文本库（本地 SQLite，无 Kafka），网关可安全调用。
        ``use_llm=False`` 时只做本地规则摘要，用于 HTTP 详情默认快路径；``True`` 可强制重新
        走 LLM 精炼。
        """

        task = self.task_store.get(task_id)
        if task is None:
            return None

        hits = self._status_hits(self._command_ids(task))
        changed = self._apply_hits(task, hits, aggregate=aggregate, use_llm=use_llm)
        if changed:
            task.updated_at = now_iso()
            self.task_store.upsert(task)
        return task

    # -- 列表（纯读，可选轻量刷新各命令状态，不聚合）--------------------- #
    def list_tasks(self, *, limit: int = 50, refresh: bool = True) -> list[VideoTask]:
        """任务列表（纯读）。``refresh=True`` 时批量读取本地回流事件并更新任务态。

        列表刷新不调用 LLM；当命令已全部回流且任务尚无答案时，会用本地事件生成规则摘要并缓存，
        使前端点击任务详情不再等待外部模型。
        """

        tasks = self.task_store.list(limit=limit)
        if not refresh:
            return tasks
        all_cmd_ids: list[str] = []
        for t in tasks:
            all_cmd_ids.extend(self._command_ids(t))
        hits = self._status_hits(all_cmd_ids)
        out: list[VideoTask] = []
        for t in tasks:
            task_hits = self._hits_for_task(t, hits)
            hit_cmds = {h.get("parent_trace_id") for h in task_hits if h.get("parent_trace_id")}
            aggregate_locally = bool(t.commands) and (
                t.returned_count == len(t.commands) or len(hit_cmds) >= len(t.commands)
            )
            changed = self._apply_hits(t, task_hits, aggregate=aggregate_locally, use_llm=False)
            if changed:
                t.updated_at = now_iso()
                self.task_store.upsert(t)
            out.append(t)
        return out
