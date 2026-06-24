import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BarChart3,
  Boxes,
  Camera,
  CheckCircle2,
  Cpu,
  Database,
  Download,
  FileText,
  GitCompare,
  LineChart,
  Play,
  RadioTower,
  RefreshCw,
  Route,
  Save,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  UploadCloud,
  Video,
  Zap
} from "lucide-react";
import { LargeTrafficMapView } from "./LargeTrafficMapView";
import { fetchSvNetwork, sendAgentNetworkCommand, SvNetworkGeometry, SvNetworkJunction } from "../api/agentNetworkClient";
import { AgentNode, NetworkSnapshot, WorldModelDefinition, WorldModelRuntime } from "../types";

interface Props {
  model: WorldModelDefinition;
  snapshot: NetworkSnapshot;
  runtime: WorldModelRuntime;
  tools: ToolDef[];
  activeToolId: string;
}

type ToolKind = "map" | "models" | "agents" | "realtime" | "metrics" | "video" | "detect" | "empty";
type ToolIcon = typeof Route;

export interface ToolDef {
  id: string;
  title: string;
  description: string;
  kind: ToolKind;
  icon: ToolIcon;
  disabled?: boolean;
}

export function getToolsForModel(model: WorldModelDefinition): ToolDef[] {
  if (model.id === "wm-video-stream") {
    return [
      { id: "video-stream", title: "视频流接入", description: "多路监控视频接入", kind: "video", icon: Video },
      { id: "object-detect", title: "目标检测", description: "边缘检测与结构化识别", kind: "detect", icon: Camera },
      { id: "video-events", title: "事件摘要", description: "语义事件汇总", kind: "empty", icon: BarChart3 },
      { id: "video-models", title: "模型管理", description: "视觉模型版本管理", kind: "empty", icon: Boxes }
    ];
  }

  if (model.id === "wm-junction-flow") {
    return [
      { id: "traffic-map", title: "交通地图", description: "真实 SV 路网主视图", kind: "map", icon: Route },
      { id: "realtime-flow", title: "实时交通数据", description: "SignalVision 实时数据", kind: "realtime", icon: LineChart },
      { id: "junction-metrics", title: "路口指标", description: "流量、速度、排队、延误", kind: "metrics", icon: BarChart3 }
    ];
  }

  // ANP 接入 SignalVision（推理/控制），不接 SignalTrain（训练）——故无模型训练/对比实验/持续学习入口。
  // 「交通数据管理」（数据批次/数据集管理）ANP 无对应后端，已移除。
  return [
    { id: "traffic-map", title: "交通地图", description: "真实 SV 路网主视图", kind: "map", icon: Route },
    { id: "realtime-flow", title: "实时交通数据", description: "SignalVision 实时态势", kind: "realtime", icon: LineChart },
    { id: "models-tool", title: "控制策略", description: "信号控制算法与策略版本", kind: "models", icon: Boxes },
    { id: "agents-tool", title: "智能体列表", description: "信号控制对象", kind: "agents", icon: RadioTower }
  ];
}

function formatNumber(value: number) {
  return value.toLocaleString("zh-CN");
}

function nowLabel() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="shell-stat-card">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

function StatusPill({ children, tone = "blue" }: { children: ReactNode; tone?: "blue" | "green" | "amber" | "slate" }) {
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="tool-shell-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function FieldGrid({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="tool-field-grid">
      {items.map(([label, value]) => (
        <label key={label}>
          <span>{label}</span>
          <input value={value} readOnly />
        </label>
      ))}
    </div>
  );
}

function ActionButton({
  icon: Icon,
  children,
  onClick,
  variant = "secondary",
  active = false
}: {
  icon: ToolIcon;
  children: ReactNode;
  onClick: () => void;
  variant?: "primary" | "secondary";
  active?: boolean;
}) {
  return (
    <button type="button" className={`tool-action-button ${variant}${active ? " active" : ""}`} onClick={onClick}>
      <Icon size={16} />
      <span>{children}</span>
    </button>
  );
}

function FeedbackBar({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="tool-feedback">
      <CheckCircle2 size={16} />
      <span>{message}</span>
    </div>
  );
}

function ActionLog({ entries }: { entries: string[] }) {
  return (
    <div className="tool-log-list">
      {entries.slice(0, 4).map((entry, index) => (
        <div className="tool-log-row" key={`${entry}-${index}`}>
          <b>{index === 0 ? "最新" : "记录"}</b>
          <span>{entry}</span>
        </div>
      ))}
    </div>
  );
}

function nodeCommandTypes(node: AgentNode | undefined): string[] {
  const raw = node?.metrics?.commandTypes ?? node?.metrics?.command_types;
  return Array.isArray(raw) ? (raw as string[]) : [];
}

type SvJunctionStatus = "online" | "warning" | "offline" | "syncing";

interface SvJunctionAgent {
  id: string;
  label: string;
  typeLabel: string;
  status: SvJunctionStatus;
  health: number;
  congestion: number;
  totalVehicles: number;
  totalHalting: number;
  x: number;
  y: number;
  raw: SvNetworkJunction;
}

function junctionTypeLabel(type: string) {
  if (type === "traffic_light") return "信号灯";
  if (type === "priority") return "优先通行";
  if (type === "right_before_left") return "右侧优先";
  return type || "未知类型";
}

function svCongestionLabel(value: number) {
  if (value >= 0.78) return "严重拥堵";
  if (value >= 0.6) return "拥堵";
  if (value >= 0.42) return "缓行";
  if (value >= 0.25) return "基本畅通";
  return "畅通";
}

function statusFromSvJunction(junction: SvNetworkJunction): SvJunctionStatus {
  if (junction.is_active === false) return "offline";
  if (junction.congestion >= 0.78) return "offline";
  if (junction.congestion >= 0.6) return "warning";
  return "online";
}

function svJunctionHealth(junction: SvNetworkJunction) {
  if (junction.is_active === false) return 0;
  return Math.max(0, Math.min(100, Math.round(100 - junction.congestion * 100)));
}

function svJunctionAgents(svNetwork?: SvNetworkGeometry | null): SvJunctionAgent[] {
  if (!svNetwork?.junctions?.length) return [];
  return [...svNetwork.junctions]
    .sort((a, b) => a.id.localeCompare(b.id, "zh-CN", { numeric: true }))
    .map((junction) => ({
      id: junction.id,
      label: `Junction ${junction.id}`,
      typeLabel: junctionTypeLabel(junction.junction_type),
      status: statusFromSvJunction(junction),
      health: svJunctionHealth(junction),
      congestion: junction.congestion,
      totalVehicles: junction.total_vehicles,
      totalHalting: junction.total_halting,
      x: junction.x,
      y: junction.y,
      raw: junction
    }));
}

function svNetworkLabel(svNetwork?: SvNetworkGeometry | null) {
  if (!svNetwork?.junctions?.length) return "SV 路网未连接";
  return `当前 SV 路网 · ${svNetwork.junctions.length} 路口 / ${svNetwork.edges.length} 道路边`;
}

// 控制策略 = 真·control_signal_inference：选算法 + 启停 → 网关 /commands → SV /api/simulation/start|stop（真驱动 SUMO）。
function ControlInferencePanel({ snapshot, svNetwork }: { snapshot: NetworkSnapshot; svNetwork?: SvNetworkGeometry | null }) {
  const ALGORITHMS = ["maxpressure", "colight", "fixedtime", "ppo"];
  const exec = snapshot.nodes.find((n) => nodeCommandTypes(n).includes("control_signal_inference"));
  const junctions = svJunctionAgents(svNetwork);
  const signalJunctions = junctions.filter((j) => j.raw.junction_type === "traffic_light").length;
  const avgCongestion = junctions.length
    ? junctions.reduce((sum, item) => sum + item.congestion, 0) / junctions.length
    : 0;
  const [algorithm, setAlgorithm] = useState("maxpressure");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([`控制策略面板就绪 · ${nowLabel()}`]);

  const dispatch = async (action: "start" | "stop") => {
    if (!exec) {
      setNotice("无在线 control_signal_inference 执行体（traffic-exec-sv-001 未注册/离线）");
      return;
    }
    setBusy(true);
    try {
      const payload = action === "start" ? { action, algorithm } : { action };
      const resp = await sendAgentNetworkCommand({
        target_agent_id: exec.id,
        command_type: "control_signal_inference",
        payload,
        expires_in_sec: 60
      });
      const msg = `已下发全局 ${action}${action === "start" ? `/${algorithm}` : ""} → ${resp.status} · cmd ${resp.command_id.slice(0, 8)} · ${nowLabel()}`;
      setNotice(msg);
      setLogs((items) => [msg, ...items]);
    } catch (error) {
      const msg = `下发失败：${error instanceof Error ? error.message : String(error)} · ${nowLabel()}`;
      setNotice(msg);
      setLogs((items) => [msg, ...items]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tool-shell-panel split wide">
      <div>
        <div className="tool-shell-header">
          <Cpu size={22} />
          <div><h2>控制策略</h2><p>选择全局信号控制算法并启停当前 SV 路网仿真（control_signal_inference → SignalVision /simulation）。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <div className="shell-stat-grid compact">
          <StatCard label="作用范围" value={junctions.length ? `${junctions.length} 路口` : "SV 未连接"} />
          <StatCard label="信号灯" value={signalJunctions} />
          <StatCard label="道路边" value={svNetwork?.edges.length ?? 0} />
          <StatCard label="平均拥堵" value={`${Math.round(avgCongestion * 100)}%`} />
        </div>
        <Section title="执行体">
          <div className="tool-list-row">
            <span className="row-main"><b>{exec?.label ?? "traffic-exec-sv-001"}</b><small>{exec ? `${exec.id} · ${nodeCommandTypes(exec).join(" / ")}` : "未发现 control_signal_inference 执行体"}</small></span>
            <StatusPill tone={exec?.status === "online" ? "green" : "amber"}>{exec?.status ?? "offline"}</StatusPill>
          </div>
        </Section>
        <Section title="全局控制配置">
          <div className="tool-config-grid">
            <label><span>控制算法</span><select value={algorithm} onChange={(event) => setAlgorithm(event.target.value)}>{ALGORITHMS.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
            <label><span>作用对象</span><input value={svNetworkLabel(svNetwork)} readOnly /></label>
          </div>
        </Section>
        <div className="tool-action-row">
          <ActionButton icon={Play} variant="primary" onClick={() => dispatch("start")}>{busy ? "下发中…" : "开始推理"}</ActionButton>
          <ActionButton icon={Square} onClick={() => dispatch("stop")}>停止推理</ActionButton>
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{algorithm}</h3>
        <FieldGrid items={[["命令", "control_signal_inference"], ["执行体", exec?.id ?? "—"], ["范围", svNetworkLabel(svNetwork)], ["执行体状态", exec?.status ?? "offline"]]} />
        <p className="muted-text">该命令与 SV 一致：算法/仿真是全局路网级配置，不绑定单个路口；网关返回「published」表示命令已入 Kafka，ack 在 Inspector / 事件流观察。</p>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

// 智能体列表：镜像 SV agents-tool，用当前 `/sv-network` 的 JunctionAgent 摘要渲染；
// ANP registry 只作为接入链路展示，不把静态 snapshot 拓扑冒充 SV 路口智能体。
function AgentsPanel({ snapshot, svNetwork }: { snapshot: NetworkSnapshot; svNetwork?: SvNetworkGeometry | null }) {
  const agents = svJunctionAgents(svNetwork);
  const anpNodes = snapshot.nodes.filter((node) => node.nodeType === "agent" || node.nodeType === "service");
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [selectedId, setSelectedId] = useState(() => agents[0]?.id ?? "");
  const [logs, setLogs] = useState<string[]>([`SV Junction Agents 刷新 · ${nowLabel()}`]);

  useEffect(() => {
    if (!agents.length) {
      setSelectedId("");
      return;
    }
    if (!agents.some((agent) => agent.id === selectedId)) setSelectedId(agents[0].id);
  }, [agents, selectedId]);

  const filteredAgents = useMemo(() => {
    const q = query.trim().toLowerCase();
    return agents.filter((agent) => {
      const matchQuery = !q || `${agent.id} ${agent.typeLabel}`.toLowerCase().includes(q);
      const matchType = !typeFilter || agent.raw.junction_type === typeFilter;
      return matchQuery && matchType;
    });
  }, [agents, query, typeFilter]);

  const selected = agents.find((node) => node.id === selectedId) ?? filteredAgents[0] ?? agents[0];
  const detailItems: Array<[string, string]> = selected
    ? [
        ["SV junction_id", selected.id],
        ["类型", selected.typeLabel],
        ["状态", selected.raw.is_active === false ? "未激活" : "活跃"],
        ["健康度", String(selected.health)],
        ["当前车辆", String(selected.totalVehicles)],
        ["停车车辆", String(selected.totalHalting)],
        ["拥堵", `${Math.round(selected.congestion * 100)}%`],
        ["位置", `${selected.x.toFixed(1)}, ${selected.y.toFixed(1)}`]
      ]
    : [["状态", "等待 SV /sv-network"]];

  const selectAgent = (agent: SvJunctionAgent) => {
    setSelectedId(agent.id);
    setLogs((items) => [`选中 SV 路口智能体 ${agent.id} · ${nowLabel()}`, ...items]);
  };

  return (
    <div className="tool-shell-panel split wide">
      <div>
        <div className="tool-shell-header">
          <RadioTower size={22} />
          <div><h2>智能体列表</h2><p>SV 当前地图 Junction Agents：来自 /sv-network（SV /junctions/summary），与交通地图同源。</p></div>
        </div>
        <div className="shell-stat-grid compact">
          <StatCard label="当前地图" value={agents.length ? `${agents.length} 路口` : "未连接"} />
          <StatCard label="道路边" value={svNetwork?.edges.length ?? 0} />
          <StatCard label="信号灯" value={agents.filter((a) => a.raw.junction_type === "traffic_light").length} />
          <StatCard label="ANP 节点" value={anpNodes.length} />
        </div>
        <div className="tool-config-grid">
          <label><span>搜索智能体</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入 SV junction_id" /></label>
          <label><span>路口类型</span><select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">全部类型</option><option value="traffic_light">信号灯</option><option value="priority">优先通行</option><option value="right_before_left">右侧优先</option></select></label>
        </div>
        <div className="agent-shell-grid">
          {filteredAgents.length === 0 ? (
            <div className="muted-text">没有符合条件的 SV 路口智能体</div>
          ) : filteredAgents.map((agent) => (
            <button type="button" className={selected?.id === agent.id ? "selected" : ""} key={agent.id} onClick={() => selectAgent(agent)}>
              <i className={`status-dot ${agent.status}`} />
              <b>{agent.label}</b>
              <span>{agent.typeLabel} · 车辆 {agent.totalVehicles} · 拥堵 {Math.round(agent.congestion * 100)}%</span>
            </button>
          ))}
        </div>
        <Section title="ANP 接入节点">
          <div className="tool-compact-list">
            {anpNodes.map((node) => (
              <div className="tool-list-row" key={node.id}>
                <span className="row-main"><b>{node.label}</b><small>{node.nodeType} · {nodeCommandTypes(node).join(" / ") || String(node.metrics?.agentType ?? node.metrics?.role ?? "read-only")}</small></span>
                <StatusPill tone={node.status === "online" ? "green" : node.status === "warning" ? "amber" : "slate"}>{node.status}</StatusPill>
              </div>
            ))}
          </div>
        </Section>
      </div>
      <div className="tool-side-card">
        <h3>{selected?.label ?? "SV 路口智能体"}</h3>
        <FieldGrid items={detailItems} />
        <p className="muted-text">SV 的 JunctionAgent 数量随当前地图变化；这里不使用网关静态拓扑，也不把全局算法控制绑定到单个路口。</p>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

// 实时交通数据：优先取 SV 当前地图 JunctionAgent 摘要；SV 不可达时回落 snapshot World Status。
function RealtimePanel({ snapshot, svNetwork }: { snapshot: NetworkSnapshot; svNetwork?: SvNetworkGeometry | null }) {
  const agg = useMemo(() => {
    const junctions = svJunctionAgents(svNetwork);
    if (junctions.length) {
      const vehicles = junctions.reduce((sum, item) => sum + item.totalVehicles, 0);
      const halting = junctions.reduce((sum, item) => sum + item.totalHalting, 0);
      const congestion = junctions.reduce((sum, item) => sum + item.congestion, 0) / junctions.length;
      const active = junctions.filter((item) => item.raw.is_active !== false).length;
      return { vehicles, halting, congestion, active, source: "sv" as const };
    }
    const regions = snapshot.nodes.filter((n) => n.nodeType === "region");
    const vehicles = regions.reduce((s, n) => s + Number(n.metrics?.flow ?? 0), 0);
    const halting = regions.reduce((m, n) => Math.max(m, Number(n.metrics?.queueM ?? 0)), 0);
    const congestion = regions.length
      ? regions.reduce((s, n) => s + Number(n.metrics?.congestionIndex ?? 0), 0) / regions.length
      : 0;
    return { vehicles, halting, congestion, active: regions.length, source: "snapshot" as const };
  }, [snapshot.nodes, svNetwork]);

  const [metric, setMetric] = useState<"vehicles" | "halting" | "congestion" | "active">("vehicles");
  const [history, setHistory] = useState<Record<string, number[]>>({ vehicles: [], halting: [], congestion: [], active: [] });

  // 每次真实 SV/snapshot 刷新（聚合值变化）追加一个采样点，前端滚动出实时曲线。
  useEffect(() => {
    const push = (arr: number[], v: number) => [...arr, v].slice(-40);
    setHistory((prev) => ({
      vehicles: push(prev.vehicles, agg.vehicles),
      halting: push(prev.halting, agg.halting),
      congestion: push(prev.congestion, agg.congestion * 100),
      active: push(prev.active, agg.active)
    }));
  }, [agg.vehicles, agg.halting, agg.congestion, agg.active]);

  const metrics = [
    { id: "vehicles", label: agg.source === "sv" ? "当前车辆" : "流量", value: formatNumber(Math.round(agg.vehicles)) },
    { id: "halting", label: agg.source === "sv" ? "停车车辆" : "排队", value: formatNumber(Math.round(agg.halting)) },
    { id: "congestion", label: "平均拥堵", value: `${Math.round(agg.congestion * 100)}%` },
    { id: "active", label: agg.source === "sv" ? "活跃路口" : "路口数", value: formatNumber(agg.active) }
  ];
  const series = history[metric] ?? [];
  const peak = Math.max(1, ...series);

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <LineChart size={22} />
        <div><h2>实时交通数据</h2><p>{agg.source === "sv" ? "SV 当前地图实时摘要：车辆/停车/拥堵/活跃路口。" : "SV 不可达，回落 World Status snapshot 聚合。"}</p></div>
      </div>
      <div className="shell-stat-grid">
        {metrics.map((item) => <StatCard key={item.id} label={item.label} value={item.value} />)}
      </div>
      <div className="tool-segment-row">
        {metrics.map((item) => <button type="button" className={metric === item.id ? "active" : ""} key={item.id} onClick={() => setMetric(item.id as typeof metric)}>{item.label}</button>)}
      </div>
      <div className="mini-chart-shell">
        {series.length === 0
          ? <span className="muted-text">等待 snapshot 采样…</span>
          : series.map((value, index) => <i key={`${index}-${value}`} style={{ height: `${Math.round((value / peak) * 100)}%` }} />)}
      </div>
      <p className="muted-text">曲线＝前端对真实 SV/snapshot 采样滚动累积（每 ~3s 一点）；冷路径 timeseries 在 v1 未启用，无 TimescaleDB 历史。</p>
      <Section title="运行事件（真实 snapshot.events）">
        <div className="live-event-list">
          {snapshot.events.length === 0
            ? <div className="live-event-row"><span className="muted-text">暂无事件</span></div>
            : snapshot.events.slice(0, 8).map((event) => <div className="live-event-row" key={event.id}><b>{event.time}</b><span>{event.title}</span></div>)}
        </div>
      </Section>
    </div>
  );
}

// 路口指标：优先取 SV 当前地图 JunctionAgent 摘要；SV 不可达时回落 snapshot region 节点。
function MetricsPanel({ snapshot, svNetwork }: { snapshot: NetworkSnapshot; svNetwork?: SvNetworkGeometry | null }) {
  const rows = useMemo(() => {
    const svRows = svJunctionAgents(svNetwork);
    if (svRows.length) {
      return svRows.map((j) => ({
        id: j.id,
        label: j.label,
        status: j.status,
        type: j.typeLabel,
        vehicles: j.totalVehicles,
        halting: j.totalHalting,
        congestionIndex: j.congestion,
        state: svCongestionLabel(j.congestion),
        source: "sv" as const,
        position: `${j.x.toFixed(1)}, ${j.y.toFixed(1)}`
      }));
    }
    return snapshot.nodes
      .filter((n) => n.nodeType === "region")
      .map((n) => ({
        id: n.id,
        label: n.label,
        status: n.status,
        type: "World Status",
        vehicles: Number(n.metrics?.flow ?? 0),
        halting: Number(n.metrics?.queueM ?? 0),
        congestionIndex: Number(n.metrics?.congestionIndex ?? 0),
        state: String(n.metrics?.state ?? "—"),
        source: "snapshot" as const,
        position: `${n.position.x.toFixed(1)}, ${n.position.y.toFixed(1)}`
      }));
  }, [snapshot.nodes, svNetwork]);
  const [selectedId, setSelectedId] = useState(rows[0]?.id ?? "");
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`指标画像刷新 · ${nowLabel()}`]);
  const selected = rows.find((item) => item.id === selectedId) ?? rows[0];

  useEffect(() => {
    if (!rows.some((r) => r.id === selectedId) && rows[0]) setSelectedId(rows[0].id);
  }, [rows, selectedId]);

  const runAction = (label: string) => {
    const message = `${label} · ${selected?.label ?? "路口"} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel split wide">
      <div>
        <div className="tool-shell-header">
          <BarChart3 size={22} />
          <div><h2>路口指标</h2><p>{rows[0]?.source === "sv" ? "SV 当前地图 JunctionAgent 摘要：车辆/停车/拥堵/类型。" : "SV 不可达，回落 World Status snapshot。"}</p></div>
        </div>
        <FeedbackBar message={notice} />
        <div className="metric-table-shell interactive-table">
          {rows.length === 0
            ? <div className="muted-text">暂无路口（snapshot 无 region 节点）</div>
            : rows.map((item) => (
              <button type="button" className={selected?.id === item.id ? "selected" : ""} key={item.id} onClick={() => setSelectedId(item.id)}>
                <b>{item.label}</b><span>{item.source === "sv" ? "车辆" : "流量"} {formatNumber(item.vehicles)}</span><span>停车 {formatNumber(item.halting)}</span><span>拥堵 {Math.round(item.congestionIndex * 100)}%</span><span>{item.state || item.status}</span>
              </button>
            ))}
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{selected?.label ?? "路口指标"}</h3>
        <FieldGrid items={[["来源", selected?.source === "sv" ? "SignalVision" : "World Status"], ["类型", selected?.type ?? "—"], [selected?.source === "sv" ? "当前车辆" : "流量", formatNumber(selected?.vehicles ?? 0)], ["停车/排队", formatNumber(selected?.halting ?? 0)], ["拥堵", selected?.state || "—"], ["拥堵指数", `${Math.round((selected?.congestionIndex ?? 0) * 100)}%`], ["位置", selected?.position ?? "—"]]} />
        <div className="tool-action-row vertical">
          <ActionButton icon={ShieldCheck} variant="primary" onClick={() => runAction("标记重点路口")}>标记重点路口</ActionButton>
          <ActionButton icon={FileText} onClick={() => runAction("生成路口画像")}>生成路口画像</ActionButton>
        </div>
        <p className="muted-text">指标优先来自 SV 当前地图；标记/画像为本地监控辅助（无下行命令）。</p>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

function VideoOpsPanel({ tool, runtime }: { tool: ToolDef; runtime: WorldModelRuntime }) {
  const Icon = tool.icon;
  const cameras = [
    { id: "cam-01", name: "民族大道北向", running: true, fps: 25 },
    { id: "cam-02", name: "雄楚大道东向", running: true, fps: 24 },
    { id: "cam-03", name: "创业街路口", running: true, fps: 25 },
    { id: "cam-04", name: "光谷一路", running: false, fps: 20 }
  ];
  const [selectedId, setSelectedId] = useState(cameras[0].id);
  const [notice, setNotice] = useState("");
  const selected = cameras.find((item) => item.id === selectedId) ?? cameras[0];

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <Icon size={22} />
        <div><h2>{tool.title}</h2><p>汇聚视频流、检测结果、语义事件和模型版本，支撑交通态势感知。</p></div>
      </div>
      <div className="shell-stat-grid">
        <StatCard label="实时摄像头" value={runtime.video.onlineCameras} />
        <StatCard label="检测任务" value={runtime.video.detectionTasks} />
        <StatCard label="事件摘要" value={runtime.video.eventCount} />
        <StatCard label="处理延迟" value={`${runtime.video.latencyMs} ms`} />
      </div>
      <FeedbackBar message={notice} />
      <div className="video-card-grid">
        {cameras.map((camera) => (
          <button type="button" className={`video-card ${selectedId === camera.id ? "selected" : ""}`} key={camera.id} onClick={() => setSelectedId(camera.id)}>
            <span><Camera size={16} /> {camera.name}</span>
            <b>{camera.running ? "实时同步" : "已归档"}</b>
            <small>{camera.fps} FPS · 1080p · H.264</small>
          </button>
        ))}
      </div>
      <div className="tool-action-row">
        <ActionButton icon={Cpu} variant="primary" onClick={() => setNotice(`检测窗口已切换 · ${selected.name} · ${nowLabel()}`)}>查看检测窗口</ActionButton>
        <ActionButton icon={FileText} onClick={() => setNotice(`事件摘要已生成 · ${selected.name} · ${nowLabel()}`)}>生成事件摘要</ActionButton>
        <ActionButton icon={Settings2} onClick={() => setNotice(`模型版本已选中 · ${tool.title} · ${nowLabel()}`)}>模型版本</ActionButton>
      </div>
      <Section title="运行链路">
        <div className="pipeline-shell">
          {["视频接入", "边缘检测", "事件聚合", "结果发布"].map((item, index) => <span key={item}><b>{index + 1}</b>{item}</span>)}
        </div>
      </Section>
    </div>
  );
}

function renderTool(
  tool: ToolDef,
  snapshot: NetworkSnapshot,
  runtime: WorldModelRuntime,
  svNetwork: SvNetworkGeometry | null,
  onSvNetworkChange: (geo: SvNetworkGeometry | null) => void
) {
  if (tool.kind === "map") return <LargeTrafficMapView search="" runtime={runtime} snapshot={snapshot} svNetwork={svNetwork} onSvNetworkChange={onSvNetworkChange} />;
  if (tool.kind === "models") return <ControlInferencePanel snapshot={snapshot} svNetwork={svNetwork} />;
  if (tool.kind === "agents") return <AgentsPanel snapshot={snapshot} svNetwork={svNetwork} />;
  if (tool.kind === "realtime") return <RealtimePanel snapshot={snapshot} svNetwork={svNetwork} />;
  if (tool.kind === "metrics") return <MetricsPanel snapshot={snapshot} svNetwork={svNetwork} />;
  if (tool.kind === "video" || tool.kind === "detect" || tool.kind === "empty") return <VideoOpsPanel tool={tool} runtime={runtime} />;
  return <VideoOpsPanel tool={tool} runtime={runtime} />;
}

export function ToolWorkspace({ model, snapshot, runtime, tools, activeToolId }: Props) {
  const availableTools = useMemo(() => (tools.length > 0 ? tools : getToolsForModel(model)), [model, tools]);
  const activeTool = availableTools.find((tool) => tool.id === activeToolId) ?? availableTools[0];
  const [svNetwork, setSvNetwork] = useState<SvNetworkGeometry | null>(null);
  const onSvNetworkChange = useCallback((geo: SvNetworkGeometry | null) => setSvNetwork(geo), []);

  useEffect(() => {
    if (model.id === "wm-video-stream") return undefined;
    let cancelled = false;
    const load = async () => {
      const geo = await fetchSvNetwork();
      if (!cancelled) setSvNetwork(geo);
    };
    load();
    const id = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [model.id]);

  return (
    <section className="tool-workspace">
      <div className={activeTool?.kind === "map" ? "tool-detail map-detail" : "tool-detail"}>
        {activeTool ? renderTool(activeTool, snapshot, runtime, svNetwork, onSvNetworkChange) : null}
      </div>
    </section>
  );
}
