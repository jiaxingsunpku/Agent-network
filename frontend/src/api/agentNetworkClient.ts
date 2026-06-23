import {
  AgentEdge,
  AgentNode,
  PhysicalResource,
  SelectionRef,
  NetworkEvent,
  AgentNetworkCommandType,
  NetworkSnapshot
} from "../types";

const env = import.meta.env as Record<string, string | undefined>;
const LEGACY_API_BASE_ENV = "VITE_" + "WORLD" + "_MODEL_API_BASE";
const API_BASE = (
  env.VITE_AGENT_NETWORK_API_BASE ??
  env[LEGACY_API_BASE_ENV] ??
  ""
).replace(/\/$/, "");
const AGENT_NETWORK_API_PREFIX = "/api/agent-network";
const LEGACY_AGENT_NETWORK_API_PREFIX = "/api/" + "world" + "-model";

async function fetchAgentNetwork(path: string, init: RequestInit) {
  const response = await fetch(`${API_BASE}${AGENT_NETWORK_API_PREFIX}${path}`, init);
  if (response.status !== 404) return response;
  return fetch(`${API_BASE}${LEGACY_AGENT_NETWORK_API_PREFIX}${path}`, init);
}

function camel<T>(value: unknown, fallback: T): T {
  return value === undefined || value === null ? fallback : (value as T);
}

function normalizeNode(raw: any): AgentNode {
  return {
    id: String(raw.id),
    label: camel(raw.label, String(raw.id)),
    nodeType: camel(raw.nodeType ?? raw.node_type, "agent") as AgentNode["nodeType"],
    group: camel(raw.group, "default"),
    position: {
      x: Number(raw.position?.x ?? 0),
      y: Number(raw.position?.y ?? 0)
    },
    status: camel(raw.status, "online") as AgentNode["status"],
    health: Number(raw.health ?? 100),
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    metrics: raw.metrics ?? {}
  };
}

function normalizeEdge(raw: any): AgentEdge {
  return {
    id: String(raw.id),
    source: String(raw.source),
    target: String(raw.target),
    label: camel(raw.label, String(raw.id)),
    directed: Boolean(raw.directed ?? true),
    relationType: camel(raw.relationType ?? raw.relation_type, "relation"),
    status: camel(raw.status, "online") as AgentEdge["status"],
    metrics: raw.metrics ?? {}
  };
}

function normalizeResource(raw: any): PhysicalResource {
  return {
    id: String(raw.id),
    label: camel(raw.label, String(raw.id)),
    resourceType: camel(raw.resourceType ?? raw.resource_type, "camera") as PhysicalResource["resourceType"],
    anchorAgentId: String(raw.anchorAgentId ?? raw.anchor_agent_id),
    height: Number(raw.height ?? 0),
    direction: camel(raw.direction, "input") as PhysicalResource["direction"],
    status: camel(raw.status, "online") as PhysicalResource["status"],
    metrics: raw.metrics ?? {}
  };
}

function normalizeEvent(raw: any): NetworkEvent {
  return {
    id: String(raw.id),
    severity: camel(raw.severity, "info") as NetworkEvent["severity"],
    title: camel(raw.title, String(raw.id)),
    targetId: String(raw.targetId ?? raw.target_id ?? ""),
    time: camel(raw.time, raw.event_ts ?? raw.ts ?? "")
  };
}

export function normalizeNetworkSnapshot(raw: any): NetworkSnapshot {
  return {
    version: camel(raw.version, "gateway"),
    generatedAt: camel(raw.generatedAt ?? raw.generated_at, new Date().toISOString()),
    topologyVersion: camel(raw.topologyVersion ?? raw.topology_version, "default"),
    region: camel(raw.region, "default"),
    summary: {
      agents: Number(raw.summary?.agents ?? raw.nodes?.length ?? 0),
      relations: Number(raw.summary?.relations ?? raw.edges?.length ?? 0),
      resources: Number(raw.summary?.resources ?? raw.resources?.length ?? 0),
      healthyPercent: Number(raw.summary?.healthyPercent ?? raw.summary?.healthy_percent ?? 100),
      kafkaLagMs: Number(raw.summary?.kafkaLagMs ?? raw.summary?.kafka_lag_ms ?? 0),
      updateRate: Number(raw.summary?.updateRate ?? raw.summary?.update_rate ?? 0)
    },
    nodes: Array.isArray(raw.nodes) ? raw.nodes.map(normalizeNode) : [],
    edges: Array.isArray(raw.edges) ? raw.edges.map(normalizeEdge) : [],
    resources: Array.isArray(raw.resources) ? raw.resources.map(normalizeResource) : [],
    trend: Array.isArray(raw.trend) ? raw.trend : [],
    events: Array.isArray(raw.events) ? raw.events.map(normalizeEvent) : []
  };
}

export async function fetchNetworkSnapshot(scope?: string): Promise<NetworkSnapshot | null> {
  const query = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  const response = await fetchAgentNetwork(`/snapshot${query}`, {
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) return null;
  if (!response.headers.get("content-type")?.includes("application/json")) return null;
  const data = await response.json();
  return normalizeNetworkSnapshot(data);
}

export interface SvNetworkJunction {
  id: string;
  x: number;
  y: number;
  congestion: number;
  junction_type: string;
  total_vehicles: number;
  total_halting: number;
}

export interface SvNetworkEdge {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  lanes: number;
  length: number;
}

export interface SvNetworkGeometry {
  ok: boolean;
  source?: string;
  junctions: SvNetworkJunction[];
  edges: SvNetworkEdge[];
  bounds: { minX: number; maxX: number; minY: number; maxY: number };
  junction_count: number;
}

/** 真实 SV 路网几何（网关 /sv-network relay SV /api/network）。不可达/未启用时返回 null（前端回落静态图）。 */
export async function fetchSvNetwork(): Promise<SvNetworkGeometry | null> {
  try {
    const response = await fetchAgentNetwork("/sv-network", { cache: "no-store", headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    if (!response.headers.get("content-type")?.includes("application/json")) return null;
    const data = await response.json();
    if (!data?.ok || !Array.isArray(data.edges) || !Array.isArray(data.junctions)) return null;
    return data as SvNetworkGeometry;
  } catch {
    return null;
  }
}

export interface SvMapEntry {
  name: string;
  path: string;
  size?: number;
}

/** SV 可用路网地图列表（网关 /sv-maps relay SV /api/maps）。不可达/未启用时返回 []（前端不显示切图下拉）。 */
export async function fetchSvMaps(): Promise<SvMapEntry[]> {
  try {
    const response = await fetchAgentNetwork("/sv-maps", { cache: "no-store", headers: { Accept: "application/json" } });
    if (!response.ok) return [];
    if (!response.headers.get("content-type")?.includes("application/json")) return [];
    const data = await response.json();
    if (!data?.ok || !Array.isArray(data.maps)) return [];
    return (data.maps as any[])
      .filter((m) => m && typeof m.path === "string" && m.path)
      .map((m) => ({ name: String(m.name ?? m.path), path: String(m.path), size: Number(m.size ?? 0) }));
  } catch {
    return [];
  }
}

export interface InspectorBlock {
  type: "metric_grid" | "kv_list" | "event_list" | "timeseries" | "json" | string;
  title?: string;
  items?: unknown[];
  value?: unknown;
  data?: unknown;
}

export interface InspectorTab {
  id: string;
  title: string;
  blocks: InspectorBlock[];
}

export interface InspectorProjection {
  target: {
    kind: SelectionRef["kind"];
    id: string;
    title: string;
  };
  tabs: InspectorTab[];
}

export interface EdgeInferenceRequest {
  agent_id: string;
  message?: string;
  mode?: "auto" | "captured";
  context?: Record<string, unknown>;
}

export interface EdgeInferenceResponse {
  ok: boolean;
  agent_id: string;
  mode: string;
  live_error?: string | null;
  result?: Record<string, unknown> | null;
  projection?: InspectorProjection;
  error?: {
    code?: string;
    message?: string;
  };
}

export async function fetchInspectorProjection(selection: SelectionRef): Promise<InspectorProjection | null> {
  const query = new URLSearchParams({ kind: selection.kind, id: selection.id });
  const response = await fetchAgentNetwork(`/projection?${query.toString()}`, {
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) return null;
  if (!response.headers.get("content-type")?.includes("application/json")) return null;
  const data = await response.json();
  if (!data?.target || !Array.isArray(data.tabs)) return null;
  return data as InspectorProjection;
}

export async function runEdgeInference(request: EdgeInferenceRequest): Promise<EdgeInferenceResponse> {
  const response = await fetchAgentNetwork("/edge-inference", {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(request)
  });
  const data = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok || !data?.ok) {
    const message = data?.error?.message ?? `HTTP ${response.status}`;
    throw new Error(String(message));
  }
  return data as EdgeInferenceResponse;
}

export interface AgentNetworkCommandRequest {
  target_agent_id: string;
  command_type: AgentNetworkCommandType;
  payload?: Record<string, unknown>;
  site_id?: string;
  region_id?: string;
  object_id?: string;
  expires_in_sec?: number;
}

export interface AgentNetworkCommandResponse {
  ok: boolean;
  command_id: string;
  topic: string;
  target: {
    agent_id?: string;
    region_id?: string;
    broadcast?: boolean;
  };
  status: "published" | string;
  message_id?: string;
}

function hasOwn(value: object, key: string) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function assertScopedCommandRequest(request: AgentNetworkCommandRequest) {
  const extra = request as AgentNetworkCommandRequest & Record<string, unknown>;
  if (!request.target_agent_id?.trim()) {
    throw new Error("target_agent_id is required");
  }
  if (hasOwn(extra, "broadcast")) {
    throw new Error("broadcast commands are not allowed from the frontend");
  }
  if (hasOwn(extra, "agent_ids")) {
    throw new Error("agent_ids commands are not allowed from the frontend");
  }
}

export async function sendAgentNetworkCommand(request: AgentNetworkCommandRequest): Promise<AgentNetworkCommandResponse> {
  assertScopedCommandRequest(request);
  const response = await fetchAgentNetwork("/commands", {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json"
    },
    body: JSON.stringify(request)
  });
  const data = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok || !data?.ok) {
    const message = data?.error?.message ?? `HTTP ${response.status}`;
    throw new Error(String(message));
  }
  return data as AgentNetworkCommandResponse;
}

export interface TimeseriesErrorSummary {
  code: string;
  message: string;
  detail?: string;
}

export type TimeseriesResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: TimeseriesErrorSummary; payload?: unknown };

export interface TimeseriesHealthResponse {
  ok: true;
  service: "timeseries" | string;
  db_connected: boolean;
  driver_available?: boolean;
  raw_events?: number;
  sv_metrics?: number;
  [key: string]: unknown;
}

export interface TimeseriesLatestResponse {
  ok: true;
  agent_id: string;
  latest_sv_metric?: Record<string, unknown> | null;
  latest_heartbeat?: Record<string, unknown> | null;
  latest_runtime_metric?: Record<string, unknown> | null;
  latest_command_ack?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface TimeseriesSummaryPoint {
  bucket_ts?: string;
  bucketTs?: string;
  total_vehicles_avg?: number | string | null;
  total_waiting_avg?: number | string | null;
  avg_speed?: number | string | null;
  congestion_avg?: number | string | null;
  current_step_max?: number | string | null;
  total_steps_max?: number | string | null;
  sample_count?: number | string | null;
  [key: string]: unknown;
}

export interface TimeseriesSummaryResponse {
  ok: true;
  agent_id: string;
  bucket: string;
  from?: string | null;
  to?: string | null;
  points: TimeseriesSummaryPoint[];
  [key: string]: unknown;
}

export interface TimeseriesEventSummary {
  event_ts?: string;
  eventTs?: string;
  ingest_ts?: string;
  topic?: string;
  message_id?: string;
  event_type?: string;
  agent_id?: string;
  payload_summary?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TimeseriesEventsResponse {
  ok: true;
  agent_id: string;
  limit: number;
  events: TimeseriesEventSummary[];
  [key: string]: unknown;
}

async function fetchTimeseriesJson<T>(path: string): Promise<TimeseriesResult<T>> {
  const networkPath = path.startsWith(AGENT_NETWORK_API_PREFIX)
    ? path.slice(AGENT_NETWORK_API_PREFIX.length)
    : path;
  const response = await fetchAgentNetwork(networkPath, {
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await response.json() : null;
  if (!response.ok || data?.ok === false || !isJson) {
    const error = data?.error && typeof data.error === "object" ? data.error as Record<string, unknown> : {};
    return {
      ok: false,
      status: response.status,
      error: {
        code: String(error.code ?? `http_${response.status}`),
        message: String(error.message ?? "Timeseries request failed"),
        detail: error.detail === undefined ? undefined : String(error.detail)
      },
      payload: data
    };
  }
  return { ok: true, status: response.status, data: data as T };
}

export function fetchTimeseriesHealth(): Promise<TimeseriesResult<TimeseriesHealthResponse>> {
  return fetchTimeseriesJson<TimeseriesHealthResponse>("/api/agent-network/timeseries/health");
}

export function fetchTimeseriesLatest(agentId: string): Promise<TimeseriesResult<TimeseriesLatestResponse>> {
  const query = new URLSearchParams({ agent_id: agentId });
  return fetchTimeseriesJson<TimeseriesLatestResponse>(`/api/agent-network/timeseries/latest?${query.toString()}`);
}

export function fetchTimeseriesSummary(
  agentId: string,
  options: { from?: string; to?: string; bucket?: string } = {}
): Promise<TimeseriesResult<TimeseriesSummaryResponse>> {
  const query = new URLSearchParams({
    agent_id: agentId,
    bucket: options.bucket ?? "1 minute"
  });
  if (options.from) query.set("from", options.from);
  if (options.to) query.set("to", options.to);
  return fetchTimeseriesJson<TimeseriesSummaryResponse>(`/api/agent-network/timeseries/summary?${query.toString()}`);
}

export function fetchTimeseriesEvents(
  agentId: string,
  limit = 20
): Promise<TimeseriesResult<TimeseriesEventsResponse>> {
  const query = new URLSearchParams({ agent_id: agentId, limit: String(limit) });
  return fetchTimeseriesJson<TimeseriesEventsResponse>(`/api/agent-network/timeseries/events?${query.toString()}`);
}
