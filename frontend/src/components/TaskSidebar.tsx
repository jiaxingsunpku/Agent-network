import { FormEvent, useCallback, useEffect, useState } from "react";
import { AlertCircle, Loader2, Radio, RefreshCw, Send } from "lucide-react";
import {
  CommandModule,
  CreateTaskRequest,
  createVideoTask,
  getVideoTask,
  listCommandModules,
  listVideoTasks,
  VideoTask
} from "../api/videoTextClient";
import { InjectedResult } from "./VideoQAPanel";
import { LocationCameraPicker, LocationSelection } from "./LocationCameraPicker";

// 协作任务侧栏（P9）：「发布命令 / 新建任务」入口 + 协作任务列表。
// 新建任务 → 网关编排器扇出 N 条定向命令调度 vision hub；列表显示每个参与 hub 的状态；
// 点任务把聚合答案 + 证据回灌问答主界面（onPickResult）。ANP 只下命令/收文本/做聚合。

interface Props {
  onPickResult: (injected: InjectedResult) => void;
}

const STATUS_LABEL: Record<string, string> = {
  pending: "待发起",
  running: "进行中",
  aggregated: "已聚合",
  failed: "失败"
};
const STATUS_TONE: Record<string, string> = {
  pending: "slate",
  running: "amber",
  aggregated: "green",
  failed: "red"
};
const CMD_LABEL: Record<string, string> = {
  pending: "待发",
  dispatched: "已下发",
  returned: "已回流",
  failed: "失败"
};

function taskToInjected(task: VideoTask): InjectedResult {
  return {
    title: task.prompt,
    result: {
      answer: task.answer || "该任务尚未聚合出答案（等待 vision hub 回流文本）。",
      tool_calls: [],
      evidence: task.evidence || [],
      warnings: task.warnings || []
    }
  };
}

export function TaskSidebar({ onPickResult }: Props) {
  const [modules, setModules] = useState<CommandModule[]>([]);
  const [moduleKey, setModuleKey] = useState("request_video_text");
  const [loc, setLoc] = useState<LocationSelection>({});
  const [prompt, setPrompt] = useState("该路段最近有没有事故、拥堵或违章？");
  const [tasks, setTasks] = useState<VideoTask[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  const activeModule = modules.find((m) => m.key === moduleKey);

  const refreshTasks = useCallback(async () => {
    try {
      setTasks(await listVideoTasks());
    } catch {
      /* 列表刷新失败不打断（保留上次） */
    }
  }, []);

  useEffect(() => {
    listCommandModules()
      .then((mods) => {
        setModules(mods);
        const firstReal = mods.find((m) => m.implemented);
        if (firstReal) setModuleKey(firstReal.key);
      })
      .catch(() => setModules([]));
  }, []);

  useEffect(() => {
    refreshTasks();
    const id = window.setInterval(refreshTasks, 4000);
    return () => window.clearInterval(id);
  }, [refreshTasks]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || creating) return;
    setCreating(true);
    setError(null);
    try {
      const req: CreateTaskRequest = {
        prompt: prompt.trim(),
        module: moduleKey,
        scope: {
          road_name: loc.road_name || undefined,
          camera_id: loc.camera_id || undefined,
          intersection_id: loc.intersection_id || undefined
        }
      };
      const task = await createVideoTask(req);
      setActiveTaskId(task.task_id);
      onPickResult(taskToInjected(task));
      await refreshTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function onPick(task: VideoTask) {
    setActiveTaskId(task.task_id);
    // 先用列表里已有的快照回灌，再拉详情（触发后端聚合）覆盖。
    onPickResult(taskToInjected(task));
    try {
      const detail = await getVideoTask(task.task_id);
      onPickResult(taskToInjected(detail));
      setTasks((prev) => prev.map((t) => (t.task_id === detail.task_id ? detail : t)));
    } catch {
      /* 详情失败保留快照 */
    }
  }

  const realModules = modules.filter((m) => m.implemented);
  const placeholderModules = modules.filter((m) => !m.implemented);

  return (
    <aside className="task-sidebar" aria-label="协作任务">
      <form className="task-create" onSubmit={onCreate}>
        <div className="task-create-head">
          <Send size={14} />
          <b>发布命令 / 新建任务</b>
        </div>
        <label className="task-field">
          <span>命令模块</span>
          <select value={moduleKey} onChange={(e) => setModuleKey(e.target.value)}>
            {realModules.map((m) => (
              <option key={m.key} value={m.key}>
                {m.title}
              </option>
            ))}
          </select>
        </label>
        {activeModule && <div className="task-module-hint">{activeModule.description}</div>}
        <LocationCameraPicker value={loc} onChange={setLoc} compact />
        <label className="task-field">
          <span>任务目标（prompt）</span>
          <textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <button type="submit" className="task-create-submit" disabled={creating}>
          {creating ? <Loader2 size={14} /> : <Radio size={14} />}
          <span>{creating ? "扇出命令中" : "扇出协作命令"}</span>
        </button>
        {error && (
          <div className="task-error">
            <AlertCircle size={13} />
            <span>{error}</span>
          </div>
        )}
        {placeholderModules.length > 0 && (
          <div className="task-placeholder-note">
            外部系统（vision hub）职责，ANP 不实现：
            {placeholderModules.map((m) => (
              <span key={m.key} className="task-placeholder-chip">
                {m.title}
              </span>
            ))}
          </div>
        )}
      </form>

      <div className="task-list-head">
        <span>协作任务</span>
        <button type="button" className="task-refresh" onClick={refreshTasks} title="刷新">
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="task-list">
        {tasks.length === 0 && <div className="task-empty">暂无协作任务。新建任务即向 vision hub 扇出定向命令。</div>}
        {tasks.map((task) => {
          const returned = task.commands.filter((c) => c.status === "returned").length;
          return (
            <button
              type="button"
              key={task.task_id}
              className={`task-card${activeTaskId === task.task_id ? " active" : ""}`}
              onClick={() => onPick(task)}
            >
              <div className="task-card-top">
                <span className="task-card-prompt">{task.prompt}</span>
                <span className={`task-status-pill ${STATUS_TONE[task.status] || "slate"}`}>
                  {STATUS_LABEL[task.status] || task.status}
                </span>
              </div>
              <div className="task-card-meta">
                <span>{task.scope.road_name || task.scope.camera_id || "—"}</span>
                <span>
                  回流 {returned}/{task.commands.length}
                </span>
              </div>
              <div className="task-card-hubs">
                {task.commands.map((cmd) => (
                  <span key={cmd.command_id} className={`task-hub cmd-${cmd.status}`} title={`${cmd.target_agent_id} · ${CMD_LABEL[cmd.status] || cmd.status}`}>
                    <i className={`hub-dot ${cmd.status}`} />
                    {cmd.target_agent_id}
                  </span>
                ))}
              </div>
              {task.answer && <div className="task-card-answer">{task.answer}</div>}
            </button>
          );
        })}
      </div>
    </aside>
  );
}
