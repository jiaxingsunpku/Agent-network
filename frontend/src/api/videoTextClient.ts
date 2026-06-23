// 视频文本事件问答客户端（P7）。复用 /api/agent-network/video-text/*（与网关同源，
// dev/preview 由 vite 反代到网关进程，见 vite.config.ts）。响应对齐后端 QueryResponse。

const env = import.meta.env as Record<string, string | undefined>;
const API_BASE = (env.VITE_AGENT_NETWORK_API_BASE ?? "").replace(/\/$/, "");
const VIDEO_PREFIX = "/api/agent-network/video-text";

export interface VideoEvidenceItem {
  event_id: string;
  event_ts?: string | null;
  camera_id?: string | null;
  road_name?: string | null;
  intersection_id?: string | null;
  category?: string | null;
  summary?: string | null;
  text: string;
  confidence?: number | null;
  artifact_ref?: string | null;
}

export interface VideoToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface VideoQueryResponse {
  answer: string;
  tool_calls: VideoToolCall[];
  evidence: VideoEvidenceItem[];
  warnings: string[];
}

export interface VideoQueryRequest {
  question: string;
  time_from?: string;
  time_to?: string;
  road_name?: string;
  intersection_id?: string;
  camera_id?: string;
  category?: string;
  keywords?: string[];
  limit?: number;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${VIDEO_PREFIX}${path}`, {
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (data?.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      else if (data?.error?.message) message = String(data.error.message);
    } catch {
      /* 忽略解析错误 */
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function queryVideoText(req: VideoQueryRequest): Promise<VideoQueryResponse> {
  return apiFetch<VideoQueryResponse>("/query", { method: "POST", body: JSON.stringify(req) });
}

// —— 位置枚举 + 事件数据库浏览（task2）：从 ANP 文本库派生，纯读 ——

export interface CameraFacet {
  camera_id: string;
  source_id?: number | null; // wangxuan 真身稳定键（目录来源时有值）
  name?: string | null; // 真身原名（tooltip）
  camera_position?: string | null; // 方位（东北角/西南角…，目录来源时为真实值）
  event_count: number;
}

export interface IntersectionFacet {
  intersection_id?: string | null;
  intersection_name?: string | null;
  road_name?: string | null;
  district?: string | null;
  event_count: number;
  cameras: CameraFacet[];
}

export interface LocationsResponse {
  intersections: IntersectionFacet[];
  total_events: number;
}

export interface EventRecord {
  event_id: string;
  event_ts?: string | null;
  source_agent_id?: string | null;
  confidence?: number | null;
  parent_trace_id?: string | null;
  camera_id?: string | null;
  road_name?: string | null;
  intersection_id?: string | null;
  road_segment?: string | null;
  start_ts?: string | null;
  end_ts?: string | null;
  text: string;
  summary?: string | null;
  category?: string | null;
  tags: string[];
  entities: Record<string, unknown>;
  artifact_ref?: string | null;
  source_model?: string | null;
  envelope?: Record<string, unknown> | null;
}

export interface EventBrowseResponse {
  total: number;
  limit: number;
  offset: number;
  items: EventRecord[];
}

export interface BrowseEventsParams {
  limit?: number;
  offset?: number;
  intersection_id?: string;
  camera_id?: string;
  road_name?: string;
  category?: string;
  q?: string;
  time_from?: string;
  time_to?: string;
}

export async function listLocations(): Promise<LocationsResponse> {
  return apiFetch<LocationsResponse>("/locations", { method: "GET" });
}

export async function browseEvents(params: BrowseEventsParams = {}): Promise<EventBrowseResponse> {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const query = qs.toString();
  return apiFetch<EventBrowseResponse>(`/events${query ? `?${query}` : ""}`, { method: "GET" });
}

export async function getEvent(eventId: string): Promise<EventRecord> {
  return apiFetch<EventRecord>(`/events/${encodeURIComponent(eventId)}`, { method: "GET" });
}

// —— 协作视频任务（P9）：编排器扇出定向命令调度多 vision hub，回流文本聚合成态势 ——

export type TaskStatus = "pending" | "running" | "aggregated" | "failed";
export type CommandStatus = "pending" | "dispatched" | "returned" | "failed";

export interface TaskScopeInput {
  road_name?: string;
  camera_id?: string;
  intersection_id?: string;
  road_segment?: string;
  time_from?: string;
  time_to?: string;
  target_agent_ids?: string[];
}

export interface TaskCommand {
  command_id: string;
  target_agent_id: string;
  status: CommandStatus;
  returned_event_id?: string | null;
  returned_ts?: string | null;
}

export interface VideoTask {
  task_id: string;
  module: string;
  prompt: string;
  scope: TaskScopeInput;
  commands: TaskCommand[];
  status: TaskStatus;
  answer?: string | null;
  evidence: VideoEvidenceItem[];
  warnings: string[];
  created_at: string;
  updated_at: string;
}

export interface CommandModule {
  key: string;
  title: string;
  description: string;
  implemented: boolean;
  command_type?: string | null;
}

export interface CreateTaskRequest {
  prompt: string;
  module?: string;
  scope: TaskScopeInput;
}

export async function createVideoTask(req: CreateTaskRequest): Promise<VideoTask> {
  return apiFetch<VideoTask>("/tasks", { method: "POST", body: JSON.stringify(req) });
}

export async function listVideoTasks(): Promise<VideoTask[]> {
  return apiFetch<VideoTask[]>("/tasks", { method: "GET" });
}

export async function getVideoTask(taskId: string): Promise<VideoTask> {
  return apiFetch<VideoTask>(`/tasks/${encodeURIComponent(taskId)}`, { method: "GET" });
}

export async function listCommandModules(): Promise<CommandModule[]> {
  return apiFetch<CommandModule[]>("/command-modules", { method: "GET" });
}
