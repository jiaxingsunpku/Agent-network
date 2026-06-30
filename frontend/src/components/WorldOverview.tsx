import { useState, type FormEvent, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Map as MapIcon, Network, Cpu, CircleDot, Plus, X } from "lucide-react";
import { registerWorldAgents, WorldAgent, WorldChannel, WorldView, type WorldRegistrationAgentInput } from "../api/agentNetworkClient";
import { LargeTrafficMapView } from "./LargeTrafficMapView";

const STATUS_COLOR: Record<string, string> = {
  online: "#1d9e75",
  warning: "#ba7517",
  degraded: "#ba7517",
  offline: "#888780",
  syncing: "#378add"
};

function color(status: string): string {
  return STATUS_COLOR[status] ?? "#888780";
}

function statusZh(s: string): string {
  return ({ online: "在线", warning: "降级", degraded: "降级", offline: "离线", syncing: "同步" } as Record<string, string>)[s] ?? s;
}

function channelText(chs: WorldChannel[]): string {
  if (!chs.length) return "—";
  return chs.map((c) => c.topic + (c.keys.length ? ` [${c.keys.join(", ")}]` : "")).join("；");
}

function registrationTemplate(targetModelId: string): string {
  return JSON.stringify({
    source: "signalvision",
    target_model_id: targetModelId || "traffic-control",
    agents: [
      {
        agent_id: "traffic-perception-sv-001",
        agent_type: "signalvision",
        capabilities: ["perception", "traffic-observation"],
        command_types: [],
        produces: [
          { topic: "anp.traffic.perception.observation.v1", keys: ["gg-xiongchu-minzu"] }
        ],
        consumes: [],
        weight: 0.92,
        status: "online"
      },
      {
        agent_id: "traffic-exec-sv-001",
        agent_type: "signalvision",
        capabilities: ["exec", "signal-control"],
        command_types: ["set_signal_plan", "control_signal_inference", "set_signal_map"],
        produces: [
          { topic: "anp.traffic.ack.v1", keys: ["gg-xiongchu-minzu"] }
        ],
        consumes: [
          { topic: "anp.traffic.command.v1", keys: ["gg-xiongchu-minzu"] }
        ],
        weight: 0.96,
        status: "online"
      }
    ]
  }, null, 2);
}

function RegistrationPanel({
  world,
  focusModelId,
  onClose
}: {
  world: WorldView;
  focusModelId?: string | null;
  onClose: () => void;
}) {
  const initialModelId = focusModelId || world.models[0]?.modelId || "";
  const [targetModelId, setTargetModelId] = useState(initialModelId);
  const [registrationText, setRegistrationText] = useState(() => registrationTemplate(initialModelId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const parsed = JSON.parse(registrationText) as Record<string, unknown>;
      const agents = parsed.agents;
      if (!Array.isArray(agents) || agents.length === 0) {
        throw new Error("注册声明必须包含非空 agents 数组");
      }
      const target = targetModelId.trim() || (typeof parsed.target_model_id === "string" ? parsed.target_model_id : "");
      const resp = await registerWorldAgents({
        source: typeof parsed.source === "string" ? parsed.source : undefined,
        target_model_id: target || undefined,
        agents: agents as WorldRegistrationAgentInput[]
      });
      window.dispatchEvent(new Event("anp-world-refresh"));
      setSuccess(`${resp.registered.join("、")} 已接入；${resp.persistence === "world_topics" ? "已同步世界生命周期 topic" : "已写入当前网关 registry"}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="自定义接入智能体"
      style={{
        position: "absolute", inset: 0, zIndex: 20, display: "grid", placeItems: "center",
        background: "rgba(12, 20, 35, 0.32)", padding: 18
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: "min(820px, 96%)", maxHeight: "calc(100% - 24px)", overflow: "auto", background: "#fff",
          color: "#172033", border: "0.5px solid rgba(0,0,0,0.16)", borderRadius: 12,
          boxShadow: "0 18px 50px rgba(15,23,42,0.28)", padding: "18px 20px", display: "grid", gap: 14
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 36, height: 36, borderRadius: 10, background: "rgba(55,138,221,0.14)", color: "#2563eb", display: "grid", placeItems: "center" }}>
            <Plus size={20} />
          </div>
          <div style={{ flex: 1 }}>
            <strong style={{ fontSize: 18 }}>自定义接入智能体</strong>
            <div style={{ fontSize: 12, opacity: 0.62, marginTop: 2 }}>按世界协议声明 agent、能力、命令和 topic/key 覆盖范围。</div>
          </div>
          <button type="button" onClick={onClose} title="关闭" style={{ border: "none", background: "transparent", cursor: "pointer", color: "inherit", padding: 6 }}>
            <X size={20} />
          </button>
        </div>

        <label style={{ display: "grid", gap: 6, fontSize: 12, fontWeight: 700 }}>
          目标模型
          <input
            value={targetModelId}
            onChange={(event) => setTargetModelId(event.target.value)}
            list="world-registration-models"
            placeholder="traffic-control"
            style={{ height: 38, border: "1px solid rgba(100,116,139,0.28)", borderRadius: 8, padding: "0 10px", fontSize: 14, color: "inherit" }}
          />
          <datalist id="world-registration-models">
            {world.models.map((model) => <option key={model.modelId} value={model.modelId} />)}
          </datalist>
        </label>

        <label style={{ display: "grid", gap: 6, fontSize: 12, fontWeight: 700 }}>
          注册声明 JSON
          <textarea
            value={registrationText}
            onChange={(event) => setRegistrationText(event.target.value)}
            spellCheck={false}
            style={{
              minHeight: 330, resize: "vertical", border: "1px solid rgba(100,116,139,0.28)", borderRadius: 8,
              padding: 12, fontSize: 12, lineHeight: 1.55, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              color: "#0f172a", background: "#fbfdff"
            }}
          />
        </label>

        {error && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#b42318", background: "rgba(244,63,94,0.08)", border: "1px solid rgba(244,63,94,0.2)", borderRadius: 8, padding: "9px 10px" }}>
            <AlertCircle size={15} /> {error}
          </div>
        )}
        {success && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#067647", background: "rgba(22,163,74,0.08)", border: "1px solid rgba(22,163,74,0.2)", borderRadius: 8, padding: "9px 10px" }}>
            <CheckCircle2 size={15} /> {success}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button type="button" onClick={onClose} style={{ height: 38, border: "1px solid rgba(100,116,139,0.28)", background: "#fff", borderRadius: 8, padding: "0 14px", cursor: "pointer", color: "inherit" }}>
            关闭
          </button>
          <button type="submit" disabled={busy} style={{ height: 38, border: "none", background: busy ? "#93a8d8" : "#2563eb", color: "#fff", borderRadius: 8, padding: "0 16px", cursor: busy ? "wait" : "pointer", fontWeight: 700 }}>
            {busy ? "接入中" : "验证并接入"}
          </button>
        </div>
      </form>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  return <span style={{ width: 8, height: 8, borderRadius: "50%", background: color(status), display: "inline-block", flex: "0 0 auto" }} />;
}

function HealthStrip({ world }: { world: WorldView }) {
  const counts = world.agents.reduce<Record<string, number>>((acc, a) => {
    acc[a.status] = (acc[a.status] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <div style={{ display: "flex", gap: 16, fontSize: 12, opacity: 0.85, padding: "2px 2px", flexWrap: "wrap", flex: "0 0 auto" }}>
      <span><StatusDot status="online" /> 在线 {counts.online ?? 0}</span>
      <span><StatusDot status="warning" /> 降级 {(counts.warning ?? 0) + (counts.degraded ?? 0)}</span>
      <span><StatusDot status="syncing" /> 同步 {counts.syncing ?? 0}</span>
      <span><StatusDot status="offline" /> 离线 {counts.offline ?? 0}</span>
      <span><Network size={13} style={{ verticalAlign: -2 }} /> models {world.models.length}</span>
      <span style={{ opacity: 0.6 }}>更新 {new Date(world.generatedAt).toLocaleTimeString()}</span>
    </div>
  );
}

// agent 详情模板 —— 字段从 /world 填；暂无的（实时指标/最近活动）留占位，接入 agent 上报后填充。
function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 8, fontSize: 12, padding: "3px 0", lineHeight: 1.5 }}>
      <span style={{ opacity: 0.55, flex: "0 0 84px" }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, wordBreak: "break-all" }}>{children}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ borderTop: "0.5px solid rgba(0,0,0,0.12)", paddingTop: 6, marginTop: 6 }}>
      <div style={{ fontSize: 11, opacity: 0.55, marginBottom: 3 }}>{title}</div>
      {children}
    </div>
  );
}

function Placeholder({ note }: { note: string }) {
  return <div style={{ fontSize: 12, opacity: 0.4, padding: "6px 0" }}>占位 · {note}</div>;
}

function AgentDetailPanel({ agent, onClose }: { agent: WorldAgent; onClose: () => void }) {
  return (
    <div
      style={{
        position: "absolute", top: 12, right: 12, width: 320, maxHeight: "calc(100% - 24px)", overflow: "auto",
        background: "#ffffff", color: "#172033", border: "0.5px solid rgba(0,0,0,0.14)", borderRadius: 12,
        padding: "12px 14px", boxShadow: "0 8px 28px rgba(15,23,42,0.16)", zIndex: 5
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <StatusDot status={agent.status} />
        <strong style={{ fontSize: 14, flex: 1, wordBreak: "break-all" }}>{agent.id}</strong>
        <button onClick={onClose} title="关闭" style={{ border: "none", background: "transparent", cursor: "pointer", fontSize: 18, lineHeight: 1, color: "#172033", opacity: 0.5 }}>×</button>
      </div>
      <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 8 }}>智能体详情 · {agent.agentType} · {statusZh(agent.status)}</div>

      <div style={{ borderTop: "0.5px solid rgba(0,0,0,0.12)", paddingTop: 6 }}>
        <DetailRow label="能力">{agent.capabilities.length ? agent.capabilities.join("、") : "—"}</DetailRow>
        <DetailRow label="可接收命令">{agent.commandTypes.length ? agent.commandTypes.join("、") : "—"}</DetailRow>
        <DetailRow label="权重">{agent.weight}</DetailRow>
        <DetailRow label="被 model 使用">{agent.governedBy.length ? agent.governedBy.join("、") : "（未被 model 管辖）"}</DetailRow>
      </div>

      <Section title="通道">
        <DetailRow label="产出">{channelText(agent.produces)}</DetailRow>
        <DetailRow label="订阅">{channelText(agent.consumes)}</DetailRow>
      </Section>

      <Section title="实时指标"><Placeholder note="暂无数据（接 agent 上报后填充）" /></Section>
      <Section title="最近活动"><Placeholder note="暂无数据" /></Section>
    </div>
  );
}

function TopologyView({ world, onSelectModel }: { world: WorldView; onSelectModel?: (id: string) => void }) {
  const models = world.models;
  const agents = world.agents.filter((a) => a.agentType !== "model");
  const rowH = 40;
  const W = 720;
  const colModelX = 30;
  const colAgentX = 470;
  const boxW = 210;
  const H = Math.max(models.length, agents.length, 1) * rowH + 40;

  const agentY: Record<string, number> = {};
  agents.forEach((a, i) => { agentY[a.id] = 30 + i * rowH; });

  return (
    <div style={{ height: "100%", overflow: "auto", border: "0.5px solid rgba(128,128,128,0.3)", borderRadius: 12, padding: "8px 4px", background: "rgba(128,128,128,0.04)" }}>
      <div style={{ display: "flex", gap: 16, fontSize: 11, opacity: 0.6, padding: "2px 10px 6px" }}>
        <span>左：models</span><span>右：agents</span>
        <span style={{ marginLeft: "auto" }}>虚线 = 治理（model→成员）</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
        {models.map((m, i) => {
          const my = 30 + i * rowH;
          return m.members.map((mem) => {
            const ay = agentY[mem];
            if (ay === undefined) return null;
            return (
              <path
                key={`${m.modelId}-${mem}`}
                d={`M ${colModelX + boxW} ${my + 13} C ${colModelX + boxW + 60} ${my + 13}, ${colAgentX - 60} ${ay + 13}, ${colAgentX} ${ay + 13}`}
                fill="none" stroke="#7f77dd" strokeWidth={1.2} strokeDasharray="4 3" opacity={0.85}
              />
            );
          });
        })}
        {models.map((m, i) => {
          const my = 30 + i * rowH;
          return (
            <g key={m.modelId} style={{ cursor: "pointer" }} onClick={() => onSelectModel?.(m.modelId)}>
              <rect x={colModelX} y={my} width={boxW} height={26} rx={6} fill="rgba(127,119,221,0.16)" stroke={color(m.status)} strokeWidth={1} />
              <circle cx={colModelX + 12} cy={my + 13} r={4} fill={color(m.status)} />
              <text x={colModelX + 24} y={my + 17} fontSize={12} fill="currentColor">model · {m.modelId}</text>
            </g>
          );
        })}
        {agents.map((a) => {
          const ay = agentY[a.id];
          return (
            <g key={a.id}>
              <rect x={colAgentX} y={ay} width={boxW} height={26} rx={6} fill="rgba(128,128,128,0.1)" stroke={color(a.status)} strokeWidth={1} />
              <circle cx={colAgentX + 12} cy={ay + 13} r={4} fill={color(a.status)} />
              <text x={colAgentX + 24} y={ay + 17} fontSize={12} fill="currentColor">{a.id}</text>
            </g>
          );
        })}
        {models.length === 0 && <text x={20} y={24} fontSize={12} fill="currentColor" opacity={0.5}>暂无 model</text>}
      </svg>
    </div>
  );
}

export function WorldOverview({
  world,
  onSelectModel,
  focusModelId,
  onAgentSelect,
  controlOpen,
  onToggleControl
}: {
  world: WorldView | null;
  onSelectModel?: (id: string) => void;
  focusModelId?: string | null;
  onAgentSelect?: (id: string | null) => void;
  controlOpen?: boolean;
  onToggleControl?: () => void;
}) {
  const [view, setView] = useState<"map" | "topology">("map");
  const [selectedAgent, setSelectedAgent] = useState<WorldAgent | null>(null);
  const [registrationOpen, setRegistrationOpen] = useState(false);

  if (!world) {
    return (
      <div style={{ padding: 24, opacity: 0.6, fontSize: 13 }}>
        <Cpu size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
        正在连接统一世界 /world …（需 ?source=gateway 且网关在线）
      </div>
    );
  }

  // 聚焦某个 model 时：只高亮它的成员 agent，标题切换成 model，并给出控制台入口。
  const focusModel = focusModelId ? world.models.find((m) => m.modelId === focusModelId) ?? null : null;
  const memberIds = focusModel ? focusModel.members : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8, padding: "4px 2px", minHeight: 0, position: "relative" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flex: "0 0 auto" }}>
        <strong style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>
          <CircleDot size={16} /> {focusModel ? `model · ${focusModel.modelId}` : "世界总览"}
        </strong>
        {focusModel && <span style={{ fontSize: 11, opacity: 0.6 }}>管辖 {focusModel.members.length} 个成员</span>}
        {focusModel && onToggleControl && (
          <button type="button" onClick={onToggleControl}
            style={{ fontSize: 12, padding: "4px 10px", borderRadius: 8, cursor: "pointer", color: "inherit",
              border: controlOpen ? "0.5px solid rgba(55,138,221,0.6)" : "0.5px solid rgba(128,128,128,0.4)",
              background: controlOpen ? "rgba(55,138,221,0.16)" : "transparent" }}>
            {controlOpen ? "关闭控制台" : "控制台"}
          </button>
        )}
        <button type="button" onClick={() => setRegistrationOpen(true)}
          style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, padding: "4px 10px", borderRadius: 8, cursor: "pointer", color: "#2563eb",
            border: "0.5px solid rgba(37,99,235,0.45)", background: "rgba(37,99,235,0.08)", fontWeight: 700 }}>
          <Plus size={14} /> 接入智能体
        </button>
        <div style={{ marginLeft: "auto", display: "inline-flex", border: "0.5px solid rgba(128,128,128,0.4)", borderRadius: 8, overflow: "hidden" }}>
          <button type="button" onClick={() => setView("map")}
            style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 12px", fontSize: 12, border: "none", cursor: "pointer", background: view === "map" ? "rgba(55,138,221,0.18)" : "transparent", color: "inherit" }}>
            <MapIcon size={14} /> 地图
          </button>
          <button type="button" onClick={() => setView("topology")}
            style={{ display: "flex", alignItems: "center", gap: 5, padding: "5px 12px", fontSize: 12, border: "none", cursor: "pointer", background: view === "topology" ? "rgba(55,138,221,0.18)" : "transparent", color: "inherit" }}>
            <Network size={14} /> 拓扑
          </button>
        </div>
      </div>
      <HealthStrip world={world} />
      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        {view === "map" ? (
          <>
            <LargeTrafficMapView
              search=""
              worldAgents={world.agents}
              agentMode
              onAgentSelect={(a) => {
                setSelectedAgent(a);
                onAgentSelect?.(a?.id ?? null);
              }}
              selectedAgentId={selectedAgent?.id ?? null}
              focusAgentIds={memberIds}
              showOverviewPanel={Boolean(focusModel)}
            />
            {/* agent 详情只反映地图选择；控制台命令目标由 App 独立维护，二者不互相覆盖。 */}
            {selectedAgent && <AgentDetailPanel agent={selectedAgent} onClose={() => setSelectedAgent(null)} />}
          </>
        ) : (
          <TopologyView world={world} onSelectModel={onSelectModel} />
        )}
      </div>
      {registrationOpen && <RegistrationPanel world={world} focusModelId={focusModel?.modelId ?? focusModelId} onClose={() => setRegistrationOpen(false)} />}
    </div>
  );
}
