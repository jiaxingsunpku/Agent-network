import { useState, type ReactNode } from "react";
import { Map as MapIcon, Network, Cpu, CircleDot } from "lucide-react";
import { WorldAgent, WorldChannel, WorldView } from "../api/agentNetworkClient";
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8, padding: "4px 2px", minHeight: 0 }}>
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
            />
            {/* 控制台开时让位给右侧 InspectorPanel 命令闭环；关时显示轻量 agent 详情。 */}
            {selectedAgent && !controlOpen && <AgentDetailPanel agent={selectedAgent} onClose={() => setSelectedAgent(null)} />}
          </>
        ) : (
          <TopologyView world={world} onSelectModel={onSelectModel} />
        )}
      </div>
    </div>
  );
}
