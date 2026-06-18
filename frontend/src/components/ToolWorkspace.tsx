import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  BarChart3,
  Boxes,
  Camera,
  CheckCircle2,
  ClipboardCheck,
  Cpu,
  Database,
  Download,
  FileText,
  GitCompare,
  GraduationCap,
  LineChart,
  Play,
  RadioTower,
  RefreshCw,
  Route,
  Save,
  Send,
  Settings2,
  ShieldCheck,
  UploadCloud,
  Video,
  Zap
} from "lucide-react";
import { LargeTrafficMapView } from "./LargeTrafficMapView";
import { NetworkSnapshot, WorldModelDefinition, WorldModelRuntime } from "../types";

interface Props {
  model: WorldModelDefinition;
  snapshot: NetworkSnapshot;
  runtime: WorldModelRuntime;
  tools: ToolDef[];
  activeToolId: string;
}

type ToolKind = "map" | "data" | "models" | "training" | "comparison" | "continual" | "agents" | "realtime" | "metrics" | "video" | "detect" | "empty";
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
      { id: "traffic-map", title: "交通地图", description: "最大路网主视图", kind: "map", icon: Route },
      { id: "realtime-flow", title: "实时交通数据", description: "SignalVision 实时数据", kind: "realtime", icon: LineChart },
      { id: "data-tool", title: "交通数据管理", description: "批次与字段管理", kind: "data", icon: Database },
      { id: "junction-metrics", title: "路口指标", description: "流量、速度、排队、延误", kind: "metrics", icon: BarChart3 }
    ];
  }

  return [
    { id: "traffic-map", title: "交通地图", description: "最大路网主视图", kind: "map", icon: Route },
    { id: "data-tool", title: "交通数据管理", description: "数据批次与字段管理", kind: "data", icon: Database },
    { id: "models-tool", title: "模型管理", description: "控制策略与模型版本", kind: "models", icon: Boxes },
    { id: "training-tool", title: "模型训练", description: "SignalTrain 训练任务", kind: "training", icon: GraduationCap },
    { id: "comparison-tool", title: "对比实验", description: "策略评测对比", kind: "comparison", icon: GitCompare },
    { id: "continual-learning", title: "持续学习", description: "批次、更新、推送", kind: "continual", icon: RefreshCw },
    { id: "agents-tool", title: "智能体列表", description: "训练/控制对象", kind: "agents", icon: RadioTower }
  ];
}

function formatNumber(value: number) {
  return value.toLocaleString("zh-CN");
}

function nowLabel() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function simClockLabel(time: string, offsetMinutes: number) {
  const [hour = "0", minute = "0", second = "0"] = time.split(":");
  const total = (Number(hour) * 3600 + Number(minute) * 60 + Number(second) + offsetMinutes * 60 + 24 * 3600) % (24 * 3600);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
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

function TrafficDataPanel({ runtime }: { runtime: WorldModelRuntime }) {
  const batches = useMemo(() => [
    {
      id: "live",
      name: "武汉光谷 7 路口流量数据",
      meta: `${runtime.status.recordsPerMin} 条/min · flow / speed / queue / delay`,
      records: runtime.traffic.processedRecords,
      quality: "99.1%",
      status: "实时同步"
    },
    {
      id: "network",
      name: "最大 output 路网缓存",
      meta: "netdata.pkl · 207 信号路口 · 40050 道路边",
      records: 40050,
      quality: "结构完整",
      status: "已归档"
    },
    {
      id: "features",
      name: "SignalVision 历史数据归档",
      meta: "15s 聚合窗口 · 24 个检测器 · 12 条检测流",
      records: runtime.traffic.totalFlow,
      quality: "字段对齐",
      status: "已归档"
    }
  ], [runtime.status.recordsPerMin, runtime.traffic.processedRecords, runtime.traffic.totalFlow]);
  const [selectedId, setSelectedId] = useState("live");
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`系统同步窗口刷新 · ${nowLabel()}`]);
  const selected = batches.find((batch) => batch.id === selectedId) ?? batches[0];

  const runAction = (label: string) => {
    const message = `${label}完成 · ${selected.name} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <Database size={22} />
        <div><h2>交通数据管理</h2><p>统一管理路口流量、速度、排队和延误等交通数据批次。</p></div>
      </div>
      <div className="shell-stat-grid">
        <StatCard label="聚合记录" value={formatNumber(runtime.traffic.processedRecords)} />
        <StatCard label="活跃信号" value={runtime.traffic.activeSignals} />
        <StatCard label="总流量" value={formatNumber(runtime.traffic.totalFlow)} />
        <StatCard label="仿真时间" value={runtime.simTime} />
      </div>
      <FeedbackBar message={notice} />
      <Section title="数据批次">
        {batches.map((batch) => (
          <button type="button" className={`tool-list-row interactive ${selected.id === batch.id ? "selected" : ""}`} key={batch.id} onClick={() => setSelectedId(batch.id)}>
            <span className="row-main"><b>{batch.name}</b><small>{batch.meta}</small></span>
            <StatusPill tone={batch.id === "live" ? "green" : "blue"}>{batch.status}</StatusPill>
          </button>
        ))}
      </Section>
      <div className="tool-action-row">
        <ActionButton icon={RefreshCw} variant="primary" onClick={() => runAction("同步最新批次")}>同步最新批次</ActionButton>
        <ActionButton icon={ShieldCheck} onClick={() => runAction("质量检查")}>质量检查</ActionButton>
        <ActionButton icon={Download} onClick={() => runAction("归档当前数据")}>归档当前数据</ActionButton>
        <ActionButton icon={UploadCloud} onClick={() => runAction("发布训练集")}>发布训练集</ActionButton>
      </div>
      <div className="tool-preview-grid">
        <div><span>当前批次</span><b>{selected.name}</b></div>
        <div><span>记录规模</span><b>{formatNumber(selected.records)}</b></div>
        <div><span>质量评分</span><b>{selected.quality}</b></div>
        <div><span>归档位置</span><b>??? / ???</b></div>
      </div>
      <ActionLog entries={logs} />
    </div>
  );
}

function ModelsPanel() {
  const models = [
    { id: "colight", name: "CoLight 控制策略", version: "v2.8.4", score: "92.4", scene: "最大 output 路网" },
    { id: "maxpressure", name: "MaxPressure 基线", version: "v1.6.0", score: "86.7", scene: "武汉光谷 7 路口" },
    { id: "fixedtime", name: "FixedTime 基线", version: "v1.2.1", score: "72.3", scene: "全天候基准" }
  ];
  const [selectedId, setSelectedId] = useState("colight");
  const [onlineId, setOnlineId] = useState("colight");
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`策略版本索引刷新 · ${nowLabel()}`]);
  const selected = models.find((item) => item.id === selectedId) ?? models[0];

  const runAction = (label: string) => {
    if (label === "切换同步策略") setOnlineId(selected.id);
    const message = `${label} · ${selected.name} ${selected.version} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel split wide">
      <div>
        <div className="tool-shell-header">
          <Boxes size={22} />
          <div><h2>模型管理</h2><p>管理控制策略、基线模型、训练场景和归档状态。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <Section title="模型列表">
          {models.map((item) => (
            <button type="button" className={`tool-list-row interactive ${selected.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)}>
              <span className="row-main"><b>{item.name}</b><small>{item.version} · {item.scene}</small></span>
              <StatusPill tone={onlineId === item.id ? "green" : "blue"}>{onlineId === item.id ? "实时同步" : "已归档"}</StatusPill>
            </button>
          ))}
        </Section>
        <div className="tool-action-row">
          <ActionButton icon={Send} variant="primary" onClick={() => runAction("切换同步策略")}>切换同步策略</ActionButton>
          <ActionButton icon={GitCompare} onClick={() => runAction("启动评测")}>启动评测</ActionButton>
          <ActionButton icon={FileText} onClick={() => runAction("复制新版本")}>复制新版本</ActionButton>
          <ActionButton icon={Save} onClick={() => runAction("保存发布单")}>保存发布单</ActionButton>
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{selected.name}</h3>
        <FieldGrid items={[["版本", selected.version], ["适用场景", selected.scene], ["策略评分", selected.score], ["状态", onlineId === selected.id ? "实时同步" : "已归档"]]} />
        <div className="model-health-grid">
          <span><b>12</b>评测任务</span>
          <span><b>3</b>发布通道</span>
          <span><b>98%</b>稳定性</span>
        </div>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

function TrainingPanel({ runtime }: { runtime: WorldModelRuntime }) {
  const dataSources = [
    {
      id: "sim-max-output-live",
      label: "实时模拟流 · 最大路网",
      batch: "sim-max-output-live",
      origin: "实时模拟",
      detail: `${runtime.status.recordsPerMin} 条/min · ${runtime.simTime}`,
      status: "实时同步",
      records: runtime.traffic.processedRecords
    },
    {
      id: "train-guanggu-7-live",
      label: "训练集 · 光谷7路口实时批次",
      batch: "wuhan-guanggu-live",
      origin: "交通数据管理",
      detail: "7 个路口 · flow / speed / queue / delay",
      status: "已归档",
      records: runtime.traffic.processedRecords
    },
    {
      id: "train-output-net-207",
      label: "训练集 · 最大路网207信号口",
      batch: "output-net-207tls",
      origin: "交通数据管理",
      detail: "207 信号路口 · 40050 道路边 · 48657 车道",
      status: "已归档",
      records: 40050
    },
    {
      id: "train-rush-hour-7",
      label: "训练集 · 光谷早高峰压力样本",
      batch: "guanggu-rush-hour-am",
      origin: "交通数据管理",
      detail: "早高峰窗口 · 排队/延误增强样本",
      status: "已归档",
      records: Math.round(runtime.traffic.processedRecords * 0.62)
    }
  ];
  const [phase, setPhase] = useState<"running" | "archived">("running");
  const [progress, setProgress] = useState(runtime.training.progress);
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`训练任务接管 · ${runtime.training.jobName} · ${nowLabel()}`]);
  const [config, setConfig] = useState({
    algorithm: "CoLight / PPO",
    scenario: "最大 output 路网",
    dataSourceId: "sim-max-output-live",
    batchSize: "64",
    learningRate: "0.001"
  });
  const selectedSource = dataSources.find((source) => source.id === config.dataSourceId) ?? dataSources[0];

  useEffect(() => {
    setProgress((value) => Math.max(value, runtime.training.progress));
  }, [runtime.training.progress]);

  useEffect(() => {
    if (phase !== "running") return undefined;
    const id = window.setInterval(() => {
      setProgress((value) => (value >= 99 ? 18 : Math.min(99, value + 0.9)));
    }, 900);
    return () => window.clearInterval(id);
  }, [phase]);

  const updateConfig = (key: keyof typeof config, value: string) => setConfig((current) => ({ ...current, [key]: value }));
  const runAction = (label: string) => {
    const message = `${label} · ${config.algorithm} · ${selectedSource.label} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel split">
      <div>
        <div className="tool-shell-header">
          <GraduationCap size={22} />
          <div><h2>模型训练</h2><p>配置并启动交通控制策略训练任务，跟踪训练进度和策略产物。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <Section title="训练配置">
          <div className="tool-config-grid">
            <label><span>训练算法</span><select value={config.algorithm} onChange={(event) => updateConfig("algorithm", event.target.value)}><option>CoLight / PPO</option><option>MAPPO</option><option>PressLight</option></select></label>
            <label><span>路网场景</span><select value={config.scenario} onChange={(event) => updateConfig("scenario", event.target.value)}><option>最大 output 路网</option><option>武汉光谷 7 路口</option><option>早高峰压力场景</option></select></label>
            <label><span>数据来源</span><select value={config.dataSourceId} onChange={(event) => updateConfig("dataSourceId", event.target.value)}>{dataSources.map((source) => <option key={source.id} value={source.id}>{source.label}</option>)}</select></label>
            <label><span>Batch Size</span><input value={config.batchSize} onChange={(event) => updateConfig("batchSize", event.target.value)} /></label>
            <label><span>学习率</span><input value={config.learningRate} onChange={(event) => updateConfig("learningRate", event.target.value)} /></label>
            <label><span>奖励函数</span><input value="pressure + queue + delay" readOnly /></label>
          </div>
        </Section>
        <div className="tool-preview-grid">
          <div><span>当前数据集</span><b>{selectedSource.label}</b></div>
          <div><span>批次编号</span><b>{selectedSource.batch}</b></div>
          <div><span>来源系统</span><b>{selectedSource.origin}</b></div>
          <div><span>{selectedSource.status}</span><b>{formatNumber(selectedSource.records)} 条</b></div>
        </div>
        <div className="tool-action-row">
          <ActionButton icon={phase === "running" ? Download : Play} variant="primary" active={phase === "running"} onClick={() => {
            const next = phase === "running" ? "archived" : "running";
            setPhase(next);
            runAction(next === "running" ? "恢复同步" : "训练归档");
          }}>{phase === "running" ? "归档训练" : "恢复同步"}</ActionButton>
          <ActionButton icon={Save} onClick={() => runAction("保存参数")}>保存参数</ActionButton>
          <ActionButton icon={Download} onClick={() => runAction("生成检查点")}>生成检查点</ActionButton>
        </div>
      </div>
      <div className="training-status-card">
        <h3>训练状态</h3>
        <p>{phase === "running" ? "实时同步" : "已归档"}</p>
        <div className="progress-shell"><i style={{ width: `${progress}%` }} /></div>
        <code>episode {runtime.training.episode} · reward {runtime.training.reward} · loss {runtime.training.loss} · ETA {runtime.training.etaMin}min</code>
        <div className="tool-preview-grid compact">
          <div><span>进度</span><b>{progress.toFixed(1)}%</b></div>
          <div><span>数据来源</span><b>{selectedSource.status}</b></div>
        </div>
        <code>{selectedSource.detail}</code>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

function ComparisonPanel({ runtime }: { runtime: WorldModelRuntime }) {
  const strategies = ["FixedTime", "MaxPressure", "CoLight", "RL Policy"];
  const [enabled, setEnabled] = useState<string[]>(["FixedTime", "MaxPressure", "CoLight"]);
  const [scenario, setScenario] = useState("早高峰");
  const [runIndex, setRunIndex] = useState(1);
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`评测矩阵刷新 · ${nowLabel()}`]);
  const toggle = (name: string) => setEnabled((items) => (items.includes(name) ? items.filter((item) => item !== name) : [...items, name]));
  const runAction = (label: string) => {
    const message = `${label} · ${scenario} · ${enabled.join(" / ")} · ${nowLabel()}`;
    setRunIndex((value) => value + 1);
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };
  const rows = enabled.map((name, index) => ({
    name,
    delay: Math.max(16, runtime.traffic.avgDelaySec + 8 - index * 4 - runIndex * 0.7),
    queue: Math.max(42, runtime.traffic.maxQueueM + 18 - index * 11),
    score: Math.min(98, 72 + index * 8 + runIndex)
  }));

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <GitCompare size={22} />
        <div><h2>对比实验</h2><p>对不同交通控制策略进行统一评测和指标对比。</p></div>
      </div>
      <div className="shell-stat-grid">
        <StatCard label="测试场景" value="最大路网" />
        <StatCard label="平均延误" value={`${runtime.traffic.avgDelaySec}s`} />
        <StatCard label="拥堵指数" value={`${Math.round(runtime.traffic.congestionIndex * 100)}%`} />
        <StatCard label="评测轮次" value={runIndex} />
      </div>
      <FeedbackBar message={notice} />
      <Section title="评测设置">
        <div className="tool-segment-row">
          {["早高峰", "平峰", "晚高峰"].map((item) => <button type="button" className={scenario === item ? "active" : ""} key={item} onClick={() => setScenario(item)}>{item}</button>)}
        </div>
        <div className="tool-check-grid">
          {strategies.map((name) => (
            <label className="tool-check-row" key={name}>
              <input type="checkbox" checked={enabled.includes(name)} onChange={() => toggle(name)} />
              <span>{name}</span>
            </label>
          ))}
        </div>
      </Section>
      <div className="tool-action-row">
        <ActionButton icon={Play} variant="primary" onClick={() => runAction("运行对比评测")}>运行对比评测</ActionButton>
        <ActionButton icon={FileText} onClick={() => runAction("生成评测报告")}>生成评测报告</ActionButton>
      </div>
      <div className="metric-table-shell interactive-table">
        {rows.map((row) => (
          <div key={row.name}><b>{row.name}</b><span>延误 {row.delay.toFixed(1)}s</span><span>排队 {row.queue.toFixed(0)}m</span><span>评分 {row.score}</span></div>
        ))}
      </div>
      <ActionLog entries={logs} />
    </div>
  );
}

function ContinualPanel({ runtime }: { runtime: WorldModelRuntime }) {
  const automations = [
    {
      id: "auto-data-sync",
      name: "新批次接收后归档训练集",
      trigger: "新数据批次到达",
      condition: `记录增量 > ${runtime.status.recordsPerMin} 条/min`,
      action: "归档到训练库",
      target: "wuhan-guanggu-live",
      status: "实时同步"
    },
    {
      id: "auto-quality-check",
      name: "训练集质量检查",
      trigger: "训练集归档完成",
      condition: "字段完整率 >= 98%",
      action: "写入质量报告",
      target: "quality-report-latest",
      status: "实时同步"
    },
    {
      id: "auto-offline-update",
      name: "夜间策略离线更新",
      trigger: "每日 02:00",
      condition: "可用归档样本 >= 5000 条",
      action: "启动离线更新",
      target: "CoLight-PPO",
      status: "已归档"
    },
    {
      id: "auto-policy-push",
      name: "策略版本推送",
      trigger: "评测评分提升",
      condition: "延误改善 >= 5%",
      action: "推送参数版本",
      target: "signal-control-runtime",
      status: "已归档"
    }
  ];
  const [selectedAutomationId, setSelectedAutomationId] = useState(automations[0].id);
  const [enabledIds, setEnabledIds] = useState<string[]>(["auto-data-sync", "auto-quality-check"]);
  const [cycle, setCycle] = useState(18);
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`持续学习批次打开 · ${nowLabel()}`]);
  const selectedAutomation = automations.find((item) => item.id === selectedAutomationId) ?? automations[0];
  const enabled = enabledIds.includes(selectedAutomation.id);
  const automationSteps = [
    ["触发器", selectedAutomation.trigger],
    ["条件", selectedAutomation.condition],
    ["动作", selectedAutomation.action],
    ["目标", selectedAutomation.target]
  ];

  const runAction = (label: string) => {
    const message = `${label} · ${selectedAutomation.name} · ${nowLabel()}`;
    setCycle((value) => value + 1);
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };
  const toggleAutomation = () => {
    setEnabledIds((items) => {
      const next = items.includes(selectedAutomation.id)
        ? items.filter((id) => id !== selectedAutomation.id)
        : [...items, selectedAutomation.id];
      return next;
    });
    const message = `${enabled ? "自动化已归档" : "自动化实时同步"} · ${selectedAutomation.name} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <RefreshCw size={22} />
        <div><h2>持续学习</h2><p>以自动化规则管理数据归档、质量检查、离线更新和参数推送。</p></div>
      </div>
      <FeedbackBar message={notice} />

      <div className="automation-shell">
        <div className="automation-rules">
          <div className="automation-panel-title">自动化规则</div>
          {automations.map((item) => (
            <button type="button" className={`automation-rule ${selectedAutomation.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => setSelectedAutomationId(item.id)}>
              <span className="row-main"><b>{item.name}</b><small>{item.trigger} · {item.action}</small></span>
              <StatusPill tone={enabledIds.includes(item.id) ? "green" : "blue"}>{enabledIds.includes(item.id) ? "实时同步" : "已归档"}</StatusPill>
            </button>
          ))}
        </div>

        <div className="automation-detail">
          <div className="automation-detail-header">
            <div>
              <span>当前自动化</span>
              <h3>{selectedAutomation.name}</h3>
            </div>
            <button type="button" className={`automation-toggle ${enabled ? "on" : ""}`} onClick={toggleAutomation}>
              <i />
              <span>{enabled ? "实时同步" : "已归档"}</span>
            </button>
          </div>
          <div className="automation-flow">
            {automationSteps.map(([label, value], index) => (
              <div className="automation-node" key={label}>
                <b>{index + 1}</b>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          <div className="tool-action-row">
            <ActionButton icon={Play} variant="primary" onClick={() => runAction("手动触发自动化")}>手动触发</ActionButton>
            <ActionButton icon={ClipboardCheck} onClick={() => runAction("执行质量检查")}>质量检查</ActionButton>
            <ActionButton icon={UploadCloud} onClick={() => runAction("推送策略版本")}>推送策略版本</ActionButton>
            <ActionButton icon={ShieldCheck} onClick={() => runAction("生成审计记录")}>生成审计记录</ActionButton>
          </div>
        </div>
      </div>

      <div className="tool-preview-grid">
        <div><span>当前批次</span><b>wuhan-guanggu-live</b></div>
        <div><span>循环轮次</span><b>{cycle}</b></div>
        <div><span>记录规模</span><b>{formatNumber(runtime.traffic.processedRecords)}</b></div>
        <div><span>吞吐</span><b>{runtime.status.recordsPerMin} 条/min</b></div>
      </div>

      <div className="automation-run-list">
        <div><b>{simClockLabel(runtime.simTime, -1)}</b><span>数据归档自动化完成</span><StatusPill tone="green">实时同步</StatusPill></div>
        <div><b>{simClockLabel(runtime.simTime, -2)}</b><span>质量报告写入历史库</span><StatusPill tone="blue">已归档</StatusPill></div>
        <div><b>{simClockLabel(runtime.simTime, -3)}</b><span>策略版本完成评测归档</span><StatusPill tone="blue">已归档</StatusPill></div>
      </div>
      <ActionLog entries={logs} />
    </div>
  );
}

function AgentsPanel({ snapshot, runtime }: { snapshot: NetworkSnapshot; runtime: WorldModelRuntime }) {
  const agents = snapshot.nodes.slice(0, 9);
  const [selectedId, setSelectedId] = useState(agents[0]?.id ?? "");
  const selected = agents.find((node) => node.id === selectedId) ?? agents[0];
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`协作网络刷新 · ${nowLabel()}`]);

  useEffect(() => {
    if (!selectedId && agents[0]) setSelectedId(agents[0].id);
  }, [agents, selectedId]);

  const runAction = (label: string) => {
    const message = `${label} · ${selected?.label ?? "路口智能体"} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel split wide">
      <div>
        <div className="tool-shell-header">
          <RadioTower size={22} />
          <div><h2>智能体列表</h2><p>展示交通控制工作流中的路口智能体和协作对象。</p></div>
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
        <FieldGrid items={[["健康度", String(selected?.health ?? 0)], ["当前延误", `${runtime.traffic.avgDelaySec}s`], ["协作分组", selected?.group ?? "-"], ["状态", "实时同步"]]} />
        <div className="tool-action-row vertical">
          <ActionButton icon={Zap} variant="primary" onClick={() => runAction("下发相位方案")}>下发相位方案</ActionButton>
          <ActionButton icon={Activity} onClick={() => runAction("重算邻接协作")}>重算邻接协作</ActionButton>
          <ActionButton icon={ShieldCheck} onClick={() => runAction("锁定巡检")}>锁定巡检</ActionButton>
        </div>
        <ActionLog entries={logs} />
      </div>
    </div>
  );
}

function RealtimePanel({ runtime }: { runtime: WorldModelRuntime }) {
  const metrics = [
    { id: "flow", label: "流量", value: formatNumber(runtime.traffic.totalFlow) },
    { id: "speed", label: "均速", value: `${runtime.traffic.avgSpeedKmh} km/h` },
    { id: "delay", label: "延误", value: `${runtime.traffic.avgDelaySec} s` },
    { id: "queue", label: "排队", value: `${runtime.traffic.maxQueueM} m` }
  ];
  const [metric, setMetric] = useState("flow");
  const [archived, setArchived] = useState(false);
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`实时窗口刷新 · ${nowLabel()}`]);
  const runAction = (label: string) => {
    const message = `${label} · ${metrics.find((item) => item.id === metric)?.label ?? "流量"} · ${nowLabel()}`;
    setNotice(message);
    setLogs((items) => [message, ...items]);
  };

  return (
    <div className="tool-shell-panel">
      <div className="tool-shell-header">
        <LineChart size={22} />
        <div><h2>实时交通数据</h2><p>展示流量、均速、排队、延误和拥堵摘要。</p></div>
      </div>
      <div className="shell-stat-grid">
        {metrics.map((item) => <StatCard key={item.id} label={item.label} value={item.value} />)}
      </div>
      <FeedbackBar message={notice} />
      <div className="tool-segment-row">
        {metrics.map((item) => <button type="button" className={metric === item.id ? "active" : ""} key={item.id} onClick={() => setMetric(item.id)}>{item.label}</button>)}
      </div>
      <div className={`mini-chart-shell ${archived ? "paused" : ""}`}>
        {runtime.chart.map((value, index) => <i key={`${runtime.frame}-${index}`} style={{ height: `${value}%` }} />)}
      </div>
      <div className="tool-action-row">
        <ActionButton icon={archived ? Play : Download} variant="primary" onClick={() => {
          setArchived((value) => !value);
          runAction(archived ? "恢复同步" : "数据归档");
        }}>{archived ? "恢复同步" : "归档当前数据"}</ActionButton>
        <ActionButton icon={RefreshCw} onClick={() => runAction("刷新窗口")}>刷新窗口</ActionButton>
        <ActionButton icon={FileText} onClick={() => runAction("归档当前数据")}>归档当前数据</ActionButton>
      </div>
      <Section title="运行事件">
        <div className="live-event-list">
          {runtime.events.map((event) => <div className="live-event-row" key={event.id}><b>{event.time}</b><span>{event.title}</span></div>)}
        </div>
      </Section>
      <ActionLog entries={logs} />
    </div>
  );
}

function MetricsPanel({ runtime }: { runtime: WorldModelRuntime }) {
  const rows = runtime.hotIntersections.slice(0, 5);
  const [selectedId, setSelectedId] = useState(rows[0]?.id ?? "");
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([`指标画像刷新 · ${nowLabel()}`]);
  const selected = rows.find((item) => item.id === selectedId) ?? rows[0];
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
          <div><h2>路口指标</h2><p>展示流量监控世界模型关注的路口统计指标。</p></div>
        </div>
        <FeedbackBar message={notice} />
        <div className="metric-table-shell interactive-table">
          {rows.map((item) => (
            <button type="button" className={selected?.id === item.id ? "selected" : ""} key={item.id} onClick={() => setSelectedId(item.id)}>
              <b>{item.label}</b><span>流量 {formatNumber(item.flow)}</span><span>速度 {item.speedKmh}</span><span>延误 {item.delaySec}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="tool-side-card">
        <h3>{selected?.label ?? "路口指标"}</h3>
        <FieldGrid items={[["流量", formatNumber(selected?.flow ?? 0)], ["速度", `${selected?.speedKmh ?? 0} km/h`], ["延误", `${selected?.delaySec ?? 0}s`], ["拥堵指数", `${Math.round(runtime.traffic.congestionIndex * 100)}%`]]} />
        <div className="tool-action-row vertical">
          <ActionButton icon={ShieldCheck} variant="primary" onClick={() => runAction("标记重点路口")}>标记重点路口</ActionButton>
          <ActionButton icon={FileText} onClick={() => runAction("生成路口画像")}>生成路口画像</ActionButton>
          <ActionButton icon={Send} onClick={() => runAction("下发巡检任务")}>下发巡检任务</ActionButton>
        </div>
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
  if (tool.kind === "map") return <LargeTrafficMapView search="" runtime={runtime} />;
  if (tool.kind === "data") return <TrafficDataPanel runtime={runtime} />;
  if (tool.kind === "models") return <ModelsPanel />;
  if (tool.kind === "training") return <TrainingPanel runtime={runtime} />;
  if (tool.kind === "comparison") return <ComparisonPanel runtime={runtime} />;
  if (tool.kind === "continual") return <ContinualPanel runtime={runtime} />;
  if (tool.kind === "agents") return <AgentsPanel snapshot={snapshot} runtime={runtime} />;
  if (tool.kind === "realtime") return <RealtimePanel runtime={runtime} />;
  if (tool.kind === "metrics") return <MetricsPanel runtime={runtime} />;
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
