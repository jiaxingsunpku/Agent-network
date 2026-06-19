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

export async function queryVideoText(req: VideoQueryRequest): Promise<VideoQueryResponse> {
  const response = await fetch(`${API_BASE}${VIDEO_PREFIX}/query`, {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(req)
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
  return (await response.json()) as VideoQueryResponse;
}
