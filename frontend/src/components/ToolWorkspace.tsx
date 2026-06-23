import { useEffect, useMemo, useState, type ReactNode } from "react";
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
import { sendAgentNetworkCommand } from "../api/agentNetworkClient";
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

// 控制策略 = 真·control_signal_inference：选算法 + 启停 → 网关 /commands → SV /api/simulation/start|stop（真驱动 SUMO）。
function ControlInferencePanel({ snapshot }: { snapshot: NetworkSnapshot }) {
  const ALGORITHMS = ["maxpressure", "colight", "fixedtime", "ppo"];
  const exec = snapshot.nodes.find((n) => nodeCommandTypes(n).includes("control_signal_inference"));
  const regions = snapshot.nodes.filter((n) => n.nodeType === "region");
  const [algorithm, setAlgorithm] = useState("maxpressure");
  const [objectId, setObjectId] = useState(regions[0]?.id ?? "gg-xiongchu-minzu");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([`控制策略面板就绪 · ${nowLabel()}`]);

  useEffect(() => {
    if (!regions.some((r) => r.id === objectId) && regions[0]) setObjectId(regions[0].id);
  }, [regions, objectId]);

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
        object_id: objectId,
        payload,
        expires_in_sec: 60
      });
      const msg = `已下发 ${action}${action === "start" ? `/${algorithm}` : ""} → ${resp.status} · cmd ${resp.command_id.slice(0, 8)} · ${nowLabel()}`;
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
          <div><h2>控制策略</h2><p>选择信号控制算法并启停推理（真·control_signal_inference → SignalVision，驱动 SUMO）。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <Section title="执行体">
          <div className="tool-list-row">
            <span className="row-main"><b>{exec?.label ?? "traffic-exec-sv-001"}</b><small>{exec ? `${exec.id} · ${nodeCommandTypes(exec).join(" / ")}` : "未发现 control_signal_inference 执行体"}</small></span>
            <StatusPill tone={exec?.status === "online" ? "green" : "amber"}>{exec?.status ?? "offline"}</StatusPill>
          </div>
        </Section>
        <Section title="控制配置">
          <div className="tool-config-grid">
            <label><span>控制算法</span><select value={algorithm} onChange={(event) => setAlgorithm(event.target.value)}>{ALGORITHMS.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
            <label><span>目标路口</span><select value={objectId} onChange={(event) => setObjectId(event.target.value)}>{(regions.length ? regions.map((r) => ({ id: r.id, label: r.label })) : [{ id: objectId, label: objectId }]).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
          </div>
        </Section>
        <div className="tool-action-row">
          <ActionButton icon={Play} variant="primary" onClick={() => dispatch("start")}>{busy ? "下发中…" : "开始推理"}</ActionButton>
          <ActionButton icon={Square} onClick={() => dispatch("stop")}>停止推理</ActionButton>
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{algorithm}</h3>
        <FieldGrid items={[["命令", "control_signal_inference"], ["执行体", exec?.id ?? "—"], ["目标路口", objectId], ["执行体状态", exec?.status ?? "offline"]]} />
        <p className="muted-text">网关返回「published」即命令已入 Kafka；ack（completed/rejected）由执行体异步回执，可在 Inspector / 事件流观察。</p>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

// 智能体列表：节点身份/状态/健康/分组取自真实 snapshot；「下发相位方案」走真·set_signal_plan 命令闭环。
function AgentsPanel({ snapshot }: { snapshot: NetworkSnapshot }) {
  const agents = snapshot.nodes;
  const regionId = snapshot.nodes.find((n) => n.nodeType === "region")?.id ?? "gg-xiongchu-minzu";
  // 默认选中可下发命令的执行体，便于直接看到真实命令按钮可用。
  const [selectedId, setSelectedId] = useState(
    () => agents.find((n) => nodeCommandTypes(n).includes("set_signal_plan"))?.id ?? agents[0]?.id ?? ""
  );
  const selected = agents.find((node) => node.id === selectedId) ?? agents[0];
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([`协作网络刷新 · ${nowLabel()}`]);

  useEffect(() => {
    if (!selectedId && agents[0]) setSelectedId(agents[0].id);
  }, [agents, selectedId]);

  const canSignalPlan = nodeCommandTypes(selected).includes("set_signal_plan");
  // 详情按节点类型给真实字段：路口(region) 看 World Status；智能体(agent) 看 registry。
  const detailItems: Array<[string, string]> = selected?.nodeType === "region"
    ? [
        ["状态", selected.status],
        ["拥堵", String(selected.metrics?.state ?? "—")],
        ["当前延误", selected.metrics?.delaySec != null ? `${Number(selected.metrics.delaySec)}s` : "—"],
        ["排队", selected.metrics?.queueM != null ? `${Number(selected.metrics.queueM)}m` : "—"]
      ]
    : [
        ["健康度", String(selected?.health ?? 0)],
        ["状态", selected?.status ?? "-"],
        ["类型", String(selected?.metrics?.agentType ?? selected?.nodeType ?? "-")],
        ["可下发命令", nodeCommandTypes(selected).join(", ") || "—"]
      ];

  const dispatchSignalPlan = async () => {
    if (!selected || !canSignalPlan) return;
    setBusy(true);
    try {
      const resp = await sendAgentNetworkCommand({
        target_agent_id: selected.id,
        command_type: "set_signal_plan",
        object_id: regionId,
        payload: { desired_phase: "north_south_green", duration_s: 25 },
        expires_in_sec: 60
      });
      const msg = `已下发 set_signal_plan@${regionId} → ${resp.status} · cmd ${resp.command_id.slice(0, 8)} · ${nowLabel()}`;
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
          <RadioTower size={22} />
          <div><h2>智能体列表</h2><p>真实 registry / World Status：路口智能体、控制/感知执行体与网关（snapshot 实时）。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <div className="agent-shell-grid">
          {agents.map((node) => (
            <button type="button" className={selected?.id === node.id ? "selected" : ""} key={node.id} onClick={() => setSelectedId(node.id)}>
              <i className={`status-dot ${node.status}`} />
              <b>{node.label}</b>
              <span>{node.group} · health {node.health}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{selected?.label ?? "路口智能体"}</h3>
        <FieldGrid items={detailItems} />
        <div className="tool-action-row vertical">
          <ActionButton icon={Zap} variant="primary" onClick={dispatchSignalPlan}>
            {busy ? "下发中…" : canSignalPlan ? "下发相位方案" : "该节点不可下发命令"}
          </ActionButton>
        </div>
        <p className="muted-text">「下发相位方案」对可下发命令的执行体发真实 set_signal_plan（→网关 /commands→ack）；其余节点为只读监控。</p>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

// 实时交通数据：取自真实 snapshot 各路口 World Status 聚合（冷路径 timeseries 在 v1 未启用，
// 故曲线由前端对真实 snapshot 采样滚动累积，而非 TimescaleDB 历史）。
function RealtimePanel({ snapshot }: { snapshot: NetworkSnapshot }) {
  const agg = useMemo(() => {
    const regions = snapshot.nodes.filter((n) => n.nodeType === "region");
    const flow = regions.reduce((s, n) => s + Number(n.metrics?.flow ?? 0), 0);
    const speeds = regions.map((n) => Number(n.metrics?.speedKmh ?? 0)).filter((v) => v > 0);
    const speed = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : 0;
    const delays = regions.map((n) => Number(n.metrics?.delaySec ?? 0));
    const delay = delays.length ? delays.reduce((a, b) => a + b, 0) / delays.length : 0;
    const queue = regions.reduce((m, n) => Math.max(m, Number(n.metrics?.queueM ?? 0)), 0);
    return { flow, speed, delay, queue };
  }, [snapshot.nodes]);

  const [metric, setMetric] = useState<"flow" | "speed" | "delay" | "queue">("flow");
  const [history, setHistory] = useState<Record<string, number[]>>({ flow: [], speed: [], delay: [], queue: [] });

  // 每次真实 snapshot 刷新（聚合值变化）追加一个采样点，前端滚动出实时曲线。
  useEffect(() => {
    const push = (arr: number[], v: number) => [...arr, v].slice(-40);
    setHistory((prev) => ({
      flow: push(prev.flow, agg.flow),
      speed: push(prev.speed, agg.speed),
      delay: push(prev.delay, agg.delay),
      queue: push(prev.queue, agg.queue)
    }));
  }, [agg.flow, agg.speed, agg.delay, agg.queue]);

  const metrics = [
    { id: "flow", label: "流量", value: formatNumber(Math.round(agg.flow)) },
    { id: "speed", label: "均速", value: `${agg.speed.toFixed(1)} km/h` },
    { id: "delay", label: "延误", value: `${agg.delay.toFixed(1)} s` },
    { id: "queue", label: "排队", value: `${agg.queue.toFixed(0)} m` }
  ];
  const series = history[metric] ?? [];
  const peak = Math.max(1, ...series);

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <LineChart size={22} />
        <div><h2>实时交通数据</h2><p>真实 World Status 聚合（snapshot 实时）：流量/均速/延误/排队。</p></div>
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
      <p className="muted-text">曲线＝前端对真实 snapshot 采样滚动累积（每 ~3s 一点）；冷路径 timeseries 在 v1 未启用，无 TimescaleDB 历史。</p>
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

// 路口指标：取自真实 snapshot region 节点的 World Status（flow/speed/delay/queue/拥堵）。
function MetricsPanel({ snapshot }: { snapshot: NetworkSnapshot }) {
  const rows = useMemo(() => snapshot.nodes
    .filter((n) => n.nodeType === "region")
    .map((n) => ({
      id: n.id,
      label: n.label,
      status: n.status,
      flow: Number(n.metrics?.flow ?? 0),
      speedKmh: Number(n.metrics?.speedKmh ?? 0),
      delaySec: Number(n.metrics?.delaySec ?? 0),
      queueM: Number(n.metrics?.queueM ?? 0),
      state: String(n.metrics?.state ?? "—"),
      congestionIndex: Number(n.metrics?.congestionIndex ?? 0)
    })), [snapshot.nodes]);
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
          <div><h2>路口指标</h2><p>真实 World Status：各路口流量/速度/延误/排队/拥堵（snapshot 实时）。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <div className="metric-table-shell interactive-table">
          {rows.length === 0
            ? <div className="muted-text">暂无路口（snapshot 无 region 节点）</div>
            : rows.map((item) => (
              <button type="button" className={selected?.id === item.id ? "selected" : ""} key={item.id} onClick={() => setSelectedId(item.id)}>
                <b>{item.label}</b><span>流量 {formatNumber(item.flow)}</span><span>速度 {item.speedKmh}</span><span>延误 {item.delaySec}</span><span>{item.state || item.status}</span>
              </button>
            ))}
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{selected?.label ?? "路口指标"}</h3>
        <FieldGrid items={[["流量", formatNumber(selected?.flow ?? 0)], ["速度", `${selected?.speedKmh ?? 0} km/h`], ["延误", `${selected?.delaySec ?? 0}s`], ["排队", `${selected?.queueM ?? 0}m`], ["拥堵", selected?.state || "—"], ["拥堵指数", `${Math.round((selected?.congestionIndex ?? 0) * 100)}%`]]} />
        <div className="tool-action-row vertical">
          <ActionButton icon={ShieldCheck} variant="primary" onClick={() => runAction("标记重点路口")}>标记重点路口</ActionButton>
          <ActionButton icon={FileText} onClick={() => runAction("生成路口画像")}>生成路口画像</ActionButton>
        </div>
        <p className="muted-text">指标为真实 World Status；标记/画像为本地监控辅助（无下行命令）。</p>
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

function renderTool(tool: ToolDef, snapshot: NetworkSnapshot, runtime: WorldModelRuntime) {
  if (tool.kind === "map") return <LargeTrafficMapView search="" runtime={runtime} snapshot={snapshot} />;
  if (tool.kind === "models") return <ControlInferencePanel snapshot={snapshot} />;
  if (tool.kind === "agents") return <AgentsPanel snapshot={snapshot} />;
  if (tool.kind === "realtime") return <RealtimePanel snapshot={snapshot} />;
  if (tool.kind === "metrics") return <MetricsPanel snapshot={snapshot} />;
  if (tool.kind === "video" || tool.kind === "detect" || tool.kind === "empty") return <VideoOpsPanel tool={tool} runtime={runtime} />;
  return <VideoOpsPanel tool={tool} runtime={runtime} />;
}

export function ToolWorkspace({ model, snapshot, runtime, tools, activeToolId }: Props) {
  const availableTools = useMemo(() => (tools.length > 0 ? tools : getToolsForModel(model)), [model, tools]);
  const activeTool = availableTools.find((tool) => tool.id === activeToolId) ?? availableTools[0];

  return (
    <section className="tool-workspace">
      <div className={activeTool?.kind === "map" ? "tool-detail map-detail" : "tool-detail"}>
        {activeTool ? renderTool(activeTool, snapshot, runtime) : null}
      </div>
    </section>
  );
}
