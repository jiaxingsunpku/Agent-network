import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import {
  AlertTriangle,
  BarChart3,
  ChevronsRight,
  Clock3,
  Database,
  Network,
  RadioTower,
  RefreshCw,
  Send,
  ShieldCheck
} from "lucide-react";
import {
  fetchInspectorProjection,
  fetchTimeseriesEvents,
  fetchTimeseriesHealth,
  fetchTimeseriesLatest,
  fetchTimeseriesSummary,
  runEdgeInference,
  sendAgentNetworkCommand,
  type EdgeInferenceResponse,
  type InspectorBlock,
  type InspectorProjection,
  type TimeseriesEventsResponse,
  type TimeseriesHealthResponse,
  type TimeseriesLatestResponse,
  type TimeseriesResult,
  type TimeseriesSummaryResponse
} from "../api/agentNetworkClient";
import { WorldModelPanel } from "./WorldModelPanel";
import { AgentEdge, AgentNode, PhysicalResource, SelectionRef, AgentNetworkCommandType, NetworkSnapshot, WorldModelDefinition } from "../types";

interface Props {
  snapshot: NetworkSnapshot;
  selected: SelectionRef | null;
  source?: string;
  worldModels: WorldModelDefinition[];
  collapsed: boolean;
  onCollapse: () => void;
}

const statusText = {
  online: "在线",
  warning: "告警",
  offline: "离线",
  syncing: "同步"
};

const INSPECTOR_MIN_WIDTH = 340;
const INSPECTOR_DEFAULT_WIDTH = 410;
const INSPECTOR_MAX_WIDTH = 660;

type CommandUiState = "idle" | "pending" | "success" | "error";

interface AgentCommandOption {
  type: AgentNetworkCommandType;
  label: string;
  payload: Record<string, unknown>;
  expiresInSec?: number;
}

interface AgentTimeseriesState {
  loading: boolean;
  health?: TimeseriesResult<TimeseriesHealthResponse>;
  latest?: TimeseriesResult<TimeseriesLatestResponse>;
  summary?: TimeseriesResult<TimeseriesSummaryResponse>;
  events?: TimeseriesResult<TimeseriesEventsResponse>;
}

const metricLabels: Record<string, string> = {
  agents: "智能体",
  relations: "关系",
  resources: "外部资源",
  healthyPercent: "健康率",
  kafkaLagMs: "Kafka 延迟",
  updateRate: "更新速率",
  load: "负载",
  queue: "队列",
  confidence: "置信度",
  speed: "速度",
  bandwidth: "带宽",
  events: "事件",
  fps: "帧率",
  bitrate: "码率",
  samples: "样本",
  commands: "指令",
  acks: "确认",
  writes: "写入",
  lagMs: "延迟",
  objects: "对象",
  tick: "Tick",
  driftMs: "漂移",
  source_agent: "来源智能体",
  running: "运行中",
  api_reachable: "API 可达",
  sv_status: "SV 状态",
  current_step: "当前步",
  total_steps: "总步数",
  total_vehicles: "车辆",
  total_waiting: "等待",
  avg_speed: "均速",
  active_junctions: "活跃路口",
  last_command_id: "最近命令",
  last_command_type: "命令类型",
  last_command_status: "命令状态",
  last_ack_status: "ACK",
  safety_guard_decision: "Safety Guard",
  latest_event_ts: "最新事件",
  total_vehicles_avg: "平均车辆",
  total_waiting_avg: "平均等待",
  congestion_avg: "拥堵均值",
  sample_count: "样本数",
  event_ts: "事件时间",
  topic: "Topic",
  message_id: "消息 ID",
  event_type: "事件类型"
};

function hasTag(node: AgentNode, ...tags: string[]) {
  const normalized = new Set(node.tags.map((tag) => tag.toLowerCase()));
  return tags.some((tag) => normalized.has(tag.toLowerCase()));
}

function isSignalVisionNode(node: AgentNode) {
  return node.id === "signalvision-collab-001" || hasTag(node, "signalvision", "traffic-inference");
}

function isVirtualTrafficNode(node: AgentNode) {
  return node.id === "virtual-traffic-agent-001" || hasTag(node, "virtual", "traffic-control");
}

function isVisionHubEdgeNode(node: AgentNode) {
  return node.id === "traffic-situation-analyst" || hasTag(node, "visionhub", "edge-agent", "inference");
}

// 已知命令类型 → 演示用 payload 模板（网关只校验外形+白名单，业务安全在执行端 Safety Guard）。
const COMMAND_PAYLOAD_TEMPLATES: Record<string, { label: string; payload: Record<string, unknown>; expiresInSec?: number }> = {
  set_signal_plan: { label: "信号计划", payload: { desired_phase: "north_south_green", duration_s: 25 } },
  control_signal_inference: { label: "启动推理", payload: { action: "start", algorithm: "maxpressure" }, expiresInSec: 60 },
  set_signal_map: { label: "切换路网", payload: { map_path: "output/netdata.pkl" }, expiresInSec: 60 },
  set_observation_rate: { label: "观测频率", payload: { interval_sec: 2 } },
  enter_maintenance_demo: { label: "维护演示", payload: { maintenance_mode: "demo_only" } },
  "sv.inference.status": { label: "SV 状态", payload: {} },
  "sv.inference.snapshot": { label: "SV 快照", payload: {} },
  "sv.inference.start": {
    label: "SV 启动",
    payload: { config: "maxpressure", sim_name: "81", inference_mode: "inference_quick", simlen: 60, nogui: true, enable_db: false, step_delay: 0 },
    expiresInSec: 60
  },
  "sv.inference.stop": { label: "SV 停止", payload: {} }
};

// 网关 agent 节点把 registry 可下发命令放在 metrics.commandTypes（snapshot 映射，见 docs/gateway-api.md §1.1）。
function gatewayCommandTypes(node: AgentNode): string[] {
  const metrics = node.metrics as Record<string, unknown>;
  const raw = metrics.commandTypes ?? metrics.command_types;
  return Array.isArray(raw) ? raw.map(String).filter(Boolean) : [];
}

function commandOptionsForNode(node: AgentNode): AgentCommandOption[] {
  // 优先：网关 registry 声明的 commandTypes 为权威来源（契约驱动，兼容新执行体 id 如 traffic-virtual-001）。
  const declared = gatewayCommandTypes(node);
  if (declared.length) {
    return declared.map((type) => {
      const tpl = COMMAND_PAYLOAD_TEMPLATES[type];
      return {
        type: type as AgentNetworkCommandType,
        label: tpl?.label ?? type,
        payload: tpl?.payload ?? {},
        expiresInSec: tpl?.expiresInSec
      };
    });
  }
  // 兜底：老 mock 节点按 id/tag 匹配（保持纯 mock 演示行为不变）。
  if (isSignalVisionNode(node)) {
    return [
      {
        type: "sv.inference.status",
        label: "SV 状态",
        payload: {}
      },
      {
        type: "sv.inference.snapshot",
        label: "SV 快照",
        payload: {}
      },
      {
        type: "sv.inference.start",
        label: "SV 启动",
        payload: {
          config: "maxpressure",
          sim_name: "81",
          inference_mode: "inference_quick",
          simlen: 60,
          nogui: true,
          enable_db: false,
          step_delay: 0
        },
        expiresInSec: 60
      },
      {
        type: "sv.inference.stop",
        label: "SV 停止",
        payload: {}
      }
    ];
  }
  if (isVirtualTrafficNode(node)) {
    return [
      {
        type: "set_signal_plan",
        label: "信号计划",
        payload: {
          desired_phase: "north_south_green",
          duration_s: 25
        }
      },
      {
        type: "set_observation_rate",
        label: "观测频率",
        payload: {
          interval_sec: 2
        }
      },
      {
        type: "enter_maintenance_demo",
        label: "维护演示",
        payload: {
          maintenance_mode: "demo_only"
        }
      }
    ];
  }
  return [];
}

function commandScopeForNode(node: AgentNode) {
  if (isSignalVisionNode(node)) {
    return {
      site_id: "local-sv-demo",
      region_id: "signalvision-local",
      object_id: "sv-dashboard"
    };
  }
  return {
    site_id: "demo-site",
    region_id: String(node.group || "region-a"),
    object_id: String(node.metrics.object_id || node.metrics.objectId || "intersection-001")
  };
}

function compactRecord(record: Record<string, unknown> | null | undefined, keys: string[]) {
  const output: Record<string, unknown> = {};
  keys.forEach((key) => {
    const value = record?.[key];
    if (value !== undefined && value !== null && value !== "") output[key] = value;
  });
  return output;
}

function timeseriesError(result: TimeseriesResult<unknown> | undefined) {
  return result && !result.ok ? `${result.error.message} (${result.error.code})` : "";
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function metricLabel(key: string) {
  if (metricLabels[key]) return metricLabels[key];
  return key
    .replace(/([A-Z])/g, " $1")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function metricProgress(value: unknown) {
  if (typeof value !== "number") return null;
  if (value >= 0 && value <= 1) return value * 100;
  if (value >= 0 && value <= 100) return value;
  return null;
}

function formatMetricValue(key: string, value: unknown) {
  if (typeof value !== "number") return String(value);
  if (key.toLowerCase().includes("percent")) return `${Math.round(value)}%`;
  if (value > 0 && value < 1) return value.toFixed(2);
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(1);
}

function MetricList({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);
  if (!entries.length) return <p className="empty-hint">暂无指标</p>;
  return (
    <div className="metric-list">
      {entries.map(([key, value]) => {
        const progress = metricProgress(value);
        return (
        <div className="metric-item" key={key}>
          <span>{metricLabel(key)}</span>
          <b>{formatMetricValue(key, value)}</b>
          {progress !== null && (
            <em className="metric-bar">
              <i style={{ width: `${clamp(progress, 0, 100)}%` }} />
            </em>
          )}
        </div>
      );
      })}
    </div>
  );
}

function StatusBadge({ status }: { status: AgentNode["status"] }) {
  return <span className={`status-badge ${status}`}><i />{statusText[status]}</span>;
}

function blockItemsToMetrics(items: unknown[]) {
  const metrics: Record<string, unknown> = {};
  items.forEach((item, index) => {
    if (item && typeof item === "object") {
      const record = item as Record<string, unknown>;
      const label = String(record.label ?? record.key ?? record.name ?? `item_${index + 1}`);
      metrics[label] = record.value ?? record.count ?? record.status ?? JSON.stringify(record);
    } else {
      metrics[`item_${index + 1}`] = item;
    }
  });
  return metrics;
}

function ProjectionBlock({ block }: { block: InspectorBlock }) {
  if (block.type === "text" || block.type === "markdown") {
    return <div className="text-block">{String(block.value ?? block.data ?? "")}</div>;
  }

  if (block.type === "metric_grid" || block.type === "kv_list") {
    const metrics = Array.isArray(block.items)
      ? blockItemsToMetrics(block.items)
      : block.value && typeof block.value === "object"
        ? block.value as Record<string, unknown>
        : {};
    return <MetricList metrics={metrics} />;
  }

  if (block.type === "event_list") {
    const items = Array.isArray(block.items) ? block.items : [];
    return (
      <div className="event-list">
        {items.length ? items.map((item, index) => {
          const record = item && typeof item === "object" ? item as Record<string, unknown> : {};
          const severity = String(record.severity ?? "info");
          const status = record.status === undefined || record.status === null ? "" : String(record.status);
          const reason = record.reason === undefined || record.reason === null ? "" : String(record.reason);
          return (
            <div key={String(record.id ?? index)} className={`event-row ${severity}`}>
              <i className="event-dot" />
              <span>
                <b>{String(record.title ?? record.event_type ?? record.id ?? "事件")}</b>
                <small>
                  <em>{String(record.target_id ?? record.targetId ?? "")}</em>
                  {status && <em className="event-status">{status}</em>}
                  <em>{String(record.time ?? record.event_ts ?? "")}</em>
                </small>
                {reason && <small className="event-reason">{reason}</small>}
              </span>
            </div>
          );
        }) : <p className="empty-hint">暂无事件</p>}
      </div>
    );
  }

  if (block.type === "json") {
    return <pre className="json-block">{JSON.stringify(block.value ?? block.data ?? block, null, 2)}</pre>;
  }

  return <pre className="json-block">{JSON.stringify(block, null, 2)}</pre>;
}

function ProjectionPanel({ projection }: { projection: InspectorProjection }) {
  return (
    <>
      <div className="object-title-row">
        <h2>{projection.target.title}</h2>
        <span className="status-badge syncing"><i />网关</span>
      </div>
      <p className="panel-subtitle">{projection.target.kind} · {projection.target.id}</p>
      {projection.tabs.flatMap((tab) => tab.blocks.map((block, index) => (
        <div className="projection-block" key={`${tab.id}-${index}`}>
          <div className="projection-block-title">{block.title ?? tab.title}</div>
          <ProjectionBlock block={block} />
        </div>
      )))}
    </>
  );
}

function Trend({ snapshot }: { snapshot: NetworkSnapshot }) {
  const points = snapshot.trend;
  if (!points.length) return <p className="empty-hint">暂无趋势数据</p>;
  const max = Math.max(...points.map((point) => point.value), 1);
  const path = points.map((point, index) => {
    const x = (index / Math.max(1, points.length - 1)) * 100;
    const y = 36 - (point.value / max) * 32;
    return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg className="trend-chart" viewBox="0 0 100 42" preserveAspectRatio="none">
      <path d={path} fill="none" stroke="#2563eb" strokeWidth="2.4" />
      <path d={`${path} L 100 42 L 0 42 Z`} fill="rgba(37,99,235,.12)" />
    </svg>
  );
}

function TimeseriesSparkline({ points }: { points: Array<Record<string, unknown>> }) {
  const values = points.map((point) => Number(point.total_vehicles_avg ?? point.total_waiting_avg ?? 0));
  if (!values.length) return <p className="empty-hint">暂无历史数据</p>;
  const max = Math.max(...values, 1);
  const path = values.map((value, index) => {
    const x = (index / Math.max(1, values.length - 1)) * 100;
    const y = 40 - (value / max) * 34;
    return `${index === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg className="timeseries-sparkline" viewBox="0 0 100 44" preserveAspectRatio="none">
      <path d={path} fill="none" stroke="#16a34a" strokeWidth="2.2" />
      <path d={`${path} L 100 44 L 0 44 Z`} fill="rgba(22,163,74,.12)" />
    </svg>
  );
}

function AgentCommandPanel({ node, source }: { node: AgentNode; source: string }) {
  const [state, setState] = useState<CommandUiState>("idle");
  const [message, setMessage] = useState("ready");
  const options = commandOptionsForNode(node);
  const canSend = source === "gateway" && state !== "pending";
  const auditMetrics = compactRecord(node.metrics as Record<string, unknown>, [
    "last_command_id",
    "last_command_type",
    "last_command_status",
    "last_ack_status",
    "safety_guard_decision",
    "last_action_status"
  ]);

  useEffect(() => {
    setState("idle");
    setMessage("ready");
  }, [node.id]);

  const issueCommand = async (option: AgentCommandOption) => {
    if (!node.id) return;
    if (source !== "gateway") {
      setState("error");
      setMessage("gateway required");
      return;
    }
    setState("pending");
    setMessage(option.type);
    try {
      const result = await sendAgentNetworkCommand({
        target_agent_id: node.id,
        command_type: option.type,
        payload: option.payload,
        ...commandScopeForNode(node),
        expires_in_sec: option.expiresInSec ?? 30
      });
      setState("success");
      setMessage(`published ${result.command_id}`);
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "command failed");
    }
  };

  return (
    <div className="panel-card agent-command-panel" data-testid="agent-command-panel">
      <div className="card-title">
        <Send size={18} />
        单智能体命令
      </div>
      <p className="panel-subtitle">{node.label} · {node.id}</p>
      {options.length ? (
        <div className="agent-command-grid">
          {options.map((option) => (
            <button
              key={option.type}
              className={`command-button ${state}`}
              type="button"
              onClick={() => issueCommand(option)}
              disabled={!canSend}
              title={option.type}
              data-command-type={option.type}
            >
              <Send size={15} />
              <span>{state === "pending" ? "发送中" : option.label}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="empty-hint">该节点当前为只读状态</p>
      )}
      <div className={`command-feedback ${state}`}>{message}</div>
      <div className="command-audit">
        <div className="card-title">
          <ShieldCheck size={16} />
          命令回执
        </div>
        {Object.keys(auditMetrics).length ? <MetricList metrics={auditMetrics} /> : <p className="empty-hint">暂无命令回执</p>}
      </div>
    </div>
  );
}

function AgentInferencePanel({ node, source }: { node: AgentNode; source: string }) {
  const [state, setState] = useState<CommandUiState>("idle");
  const [message, setMessage] = useState("待机");
  const [result, setResult] = useState<EdgeInferenceResponse | null>(null);
  const canRun = source === "gateway" && state !== "pending";

  useEffect(() => {
    setState("idle");
    setMessage("待机");
    setResult(null);
  }, [node.id]);

  const runInference = async () => {
    if (source !== "gateway") {
      setState("error");
      setMessage("需要网关数据源");
      return;
    }
    setState("pending");
    setMessage("推理中");
    setResult(null);
    try {
      const response = await runEdgeInference({
        agent_id: node.id,
        mode: "auto",
        context: {
          source: "agent-network-frontend",
          selected_node: node.id
        }
      });
      setResult(response);
      setState("success");
      setMessage(response.mode === "edge_api" ? "已调用边缘系统" : "已读取本地结果");
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "推理失败");
    }
  };

  return (
    <div className="panel-card agent-command-panel" data-testid="edge-inference-panel">
      <div className="card-title">
        <Send size={18} />
        边缘智能体
      </div>
      <p className="panel-subtitle">{node.label} · {node.id}</p>
      <div className="agent-command-grid">
        <button
          className={`command-button ${state}`}
          type="button"
          onClick={runInference}
          disabled={!canRun}
          data-command-type="edge.inference"
        >
          <Send size={15} />
          <span>{state === "pending" ? "执行中" : "推理"}</span>
        </button>
      </div>
      <div className={`command-feedback ${state}`}>{message}</div>
      {!result && (
        <p className="empty-hint">点击后读取 VisionHub 边缘智能体运行结果；当前不会修改边缘系统代码。</p>
      )}
      {result?.projection && (
        <div className="inference-result">
          <ProjectionPanel projection={result.projection} />
        </div>
      )}
    </div>
  );
}

function AgentTimeseriesPanel({ node, source }: { node: AgentNode; source: string }) {
  const [state, setState] = useState<AgentTimeseriesState>({ loading: false });

  useEffect(() => {
    let cancelled = false;
    if (source !== "gateway") {
      setState({ loading: false });
      return () => {
        cancelled = true;
      };
    }
    const to = new Date();
    const from = new Date(to.getTime() - 60 * 60 * 1000);
    setState({ loading: true });
    Promise.all([
      fetchTimeseriesHealth(),
      fetchTimeseriesLatest(node.id),
      fetchTimeseriesSummary(node.id, { from: from.toISOString(), to: to.toISOString(), bucket: "1 minute" }),
      fetchTimeseriesEvents(node.id, 20)
    ])
      .then(([health, latest, summary, events]) => {
        if (!cancelled) setState({ loading: false, health, latest, summary, events });
      })
      .catch((error) => {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "network error";
          const failure: TimeseriesResult<TimeseriesHealthResponse> = {
            ok: false,
            status: 0,
            error: { code: "network_error", message }
          };
          setState({ loading: false, health: failure });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [node.id, source]);

  const health = state.health?.ok ? state.health.data : null;
  const latest = state.latest?.ok ? state.latest.data : null;
  const summary = state.summary?.ok ? state.summary.data : null;
  const events = state.events?.ok ? state.events.data.events : [];
  const error = timeseriesError(state.health) || timeseriesError(state.latest) || timeseriesError(state.summary) || timeseriesError(state.events);
  const latestMetric = compactRecord(latest?.latest_sv_metric, [
    "event_ts",
    "current_step",
    "total_steps",
    "total_vehicles",
    "total_waiting",
    "avg_speed",
    "congestion_avg"
  ]);
  const latestAck = compactRecord(latest?.latest_command_ack, [
    "event_ts",
    "command_id",
    "status",
    "safety_decision",
    "reason"
  ]);

  return (
    <div className="panel-card agent-timeseries-panel" data-testid="agent-timeseries-panel">
      <div className="card-title">
        <Clock3 size={18} />
        时序冷路径
      </div>
      <p className="panel-subtitle">{node.id}</p>
      {source !== "gateway" && <p className="empty-hint">时序库不可用</p>}
      {state.loading && (
        <p className="loading-line">
          <RefreshCw size={14} />
          loading
        </p>
      )}
      {error && <p className="timeseries-error">{error}</p>}
      {health && (
        <div className="timeseries-health">
          <span>db {health.db_connected ? "connected" : "unavailable"}</span>
          <span>raw {String(health.raw_events ?? 0)}</span>
          <span>sv {String(health.sv_metrics ?? 0)}</span>
        </div>
      )}
      {!error && !state.loading && source === "gateway" && !Object.keys(latestMetric).length && !Object.keys(latestAck).length && (
        <p className="empty-hint">暂无历史数据</p>
      )}
      {Object.keys(latestMetric).length > 0 && (
        <div className="timeseries-section">
          <div className="projection-block-title">最新指标</div>
          <MetricList metrics={latestMetric} />
        </div>
      )}
      {Object.keys(latestAck).length > 0 && (
        <div className="timeseries-section">
          <div className="projection-block-title">最新 ACK</div>
          <MetricList metrics={latestAck} />
        </div>
      )}
      {summary && (
        <div className="timeseries-section">
          <div className="projection-block-title">近 60 分钟趋势 · {summary.bucket}</div>
          <TimeseriesSparkline points={summary.points as Array<Record<string, unknown>>} />
        </div>
      )}
      {events.length > 0 && (
        <div className="timeseries-section">
          <div className="projection-block-title">事件摘要</div>
          <div className="event-list compact">
            {events.slice(0, 6).map((event, index) => (
              <div key={String(event.message_id ?? index)} className="event-row">
                <i className="event-dot" />
                <span>
                  <b>{String(event.event_type ?? event.topic ?? "event")}</b>
                  <small>
                    <em>{String(event.event_ts ?? event.eventTs ?? "")}</em>
                    <em>{String(event.message_id ?? "")}</em>
                  </small>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function NodePanel({ node }: { node: AgentNode }) {
  return (
    <>
      <div className="object-title-row">
        <h2>{node.label}</h2>
        <StatusBadge status={node.status} />
      </div>
      <p className="panel-subtitle">{node.id} · {node.group} · {node.tags.join(" / ")}</p>
      <div className="health-line">
        <span>健康度</span>
        <b>{node.health}%</b>
        <i style={{ width: `${node.health}%` }} />
      </div>
      <MetricList metrics={node.metrics} />
    </>
  );
}

function EdgePanel({ edge }: { edge: AgentEdge }) {
  return (
    <>
      <div className="object-title-row">
        <h2>{edge.label}</h2>
        <StatusBadge status={edge.status} />
      </div>
      <p className="panel-subtitle">{edge.source} → {edge.target} · {edge.relationType}</p>
      <MetricList metrics={edge.metrics} />
    </>
  );
}

function ResourcePanel({ resource }: { resource: PhysicalResource }) {
  return (
    <>
      <div className="object-title-row">
        <h2>{resource.label}</h2>
        <StatusBadge status={resource.status} />
      </div>
      <p className="panel-subtitle">{resource.resourceType} · {resource.direction} · anchor {resource.anchorAgentId}</p>
      <MetricList metrics={resource.metrics} />
    </>
  );
}

export function InspectorPanel({ snapshot, selected, source, worldModels, collapsed, onCollapse }: Props) {
  const [width, setWidth] = useState(INSPECTOR_DEFAULT_WIDTH);
  const [projection, setProjection] = useState<InspectorProjection | null>(null);
  const dragRef = useRef({ startX: 0, startWidth: INSPECTOR_DEFAULT_WIDTH });
  const node = selected?.kind === "node" ? snapshot.nodes.find((item) => item.id === selected.id) : null;
  const edge = selected?.kind === "edge" ? snapshot.edges.find((item) => item.id === selected.id) : null;
  const resource = selected?.kind === "resource" ? snapshot.resources.find((item) => item.id === selected.id) : null;
  const worldModel = selected?.kind === "world_model" ? worldModels.find((item) => item.id === selected.id) : null;
  const visionHubSelected = Boolean(node && isVisionHubEdgeNode(node));
  const warnings = snapshot.events.filter((event) => event.severity !== "info");
  const sourceLabel = source ?? "mock";

  useEffect(() => {
    let cancelled = false;
    setProjection(null);
    if (!selected || selected.kind === "world_model") return;
    const load = () => {
      fetchInspectorProjection(selected)
        .then((next) => {
          if (!cancelled) setProjection(next);
        })
        .catch(() => {
          if (!cancelled) setProjection(null);
        });
    };
    load();
    // 轮询刷新：命令下发后数秒内「命令闭环」tab 能看到执行端回的 ack。
    const timer = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selected]);

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragRef.current = { startX: event.clientX, startWidth: width };
    document.body.classList.add("resizing-inspector");

    const onMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      const delta = dragRef.current.startX - moveEvent.clientX;
      setWidth(clamp(dragRef.current.startWidth + delta, INSPECTOR_MIN_WIDTH, INSPECTOR_MAX_WIDTH));
    };
    const onUp = () => {
      document.body.classList.remove("resizing-inspector");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <aside className={collapsed ? "inspector collapsed" : "inspector"} style={collapsed ? undefined : { width }}>
      {!collapsed && (
        <div
          className="inspector-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整右侧信息栏宽度"
          aria-valuenow={Math.round(width)}
          aria-valuemin={INSPECTOR_MIN_WIDTH}
          aria-valuemax={INSPECTOR_MAX_WIDTH}
          onPointerDown={startResize}
        />
      )}
      <button className="collapse-handle" onClick={onCollapse} title={collapsed ? "展开详情" : "收起详情"}>
        <ChevronsRight size={18} />
      </button>
      {!collapsed && (
        <>
          <div className="inspector-header">
            <span className="inspector-kicker">分析投影 · {source ?? "mock"}</span>
            <h2>{worldModel ? worldModel.name : node ? node.label : "世界模型摘要"}</h2>
            <p>{worldModel ? "世界模型是面向功能/场景的多智能体工作流；动作在此处以演示壳呈现。" : visionHubSelected ? "边缘智能体处于待机状态，点击推理后读取运行结果。" : `展示内容由分析层 projection 决定；当前数据源：${sourceLabel}。`}</p>
          </div>

          {worldModel ? (
            <WorldModelPanel model={worldModel} snapshot={snapshot} source={sourceLabel} />
          ) : visionHubSelected && node ? (
            <AgentInferencePanel node={node} source={sourceLabel} />
          ) : (
            <>
              <div className="panel-card">
                {projection && <ProjectionPanel projection={projection} />}
                {!projection && node && <NodePanel node={node} />}
                {!projection && edge && <EdgePanel edge={edge} />}
                {!projection && resource && <ResourcePanel resource={resource} />}
                {!projection && !node && !edge && !resource && (
                  <>
                    <h2>全局态势</h2>
                    <p className="panel-subtitle">点击地图节点、关系线或 3D 资源柱查看详情。</p>
                    <MetricList metrics={{
                      agents: snapshot.summary.agents,
                      relations: snapshot.summary.relations,
                      resources: snapshot.summary.resources,
                      healthyPercent: snapshot.summary.healthyPercent,
                      kafkaLagMs: snapshot.summary.kafkaLagMs,
                      updateRate: snapshot.summary.updateRate
                    }} />
                  </>
                )}
              </div>

              {node && commandOptionsForNode(node).length > 0 && (
                <AgentCommandPanel node={node} source={sourceLabel} />
              )}
              {node && (isSignalVisionNode(node) || isVirtualTrafficNode(node)) && (
                <AgentTimeseriesPanel node={node} source={sourceLabel} />
              )}
            </>
          )}

          <div className="panel-card">
            <div className="card-title">
              <BarChart3 size={18} />
              更新负载趋势
            </div>
            <Trend snapshot={snapshot} />
          </div>

          <div className="panel-card">
            <div className="card-title">
              <AlertTriangle size={18} />
              近期事件
            </div>
            <div className="event-list">
              {warnings.length ? warnings.map((event) => (
                <div key={event.id} className={`event-row ${event.severity}`}>
                  <i className="event-dot" />
                  <span>
                    <b>{event.title}</b>
                    <small><em>{event.targetId}</em><em>{event.time}</em></small>
                  </span>
                </div>
              )) : <p className="empty-hint">暂无需要关注的事件</p>}
            </div>
          </div>

          <div className="panel-card soft">
            <div className="card-title">
              <Network size={18} />
              视图语义
            </div>
            <p className="muted-text">二维地图只表达智能体之间的一跳或多跳关系；3D 视图中平面仍表示智能体关系，竖向连线表示与物理层、存储或仿真资源的绑定。</p>
          </div>

          <div className="panel-card soft">
            <div className="card-title">
              <Database size={18} />
              对接提醒
            </div>
            <p className="muted-text"><RadioTower size={14} /> Kafka 消费部分暂不在本前端中实现，后续由接入服务提供 snapshot、diff stream 和详情 projection。</p>
          </div>
        </>
      )}
    </aside>
  );
}
