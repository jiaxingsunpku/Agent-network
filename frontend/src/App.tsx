import { useEffect, useMemo, useState } from "react";
import { LeftToolbar, ToolbarItem } from "./components/LeftToolbar";
import { ModelIntroPanel } from "./components/ModelIntroPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { getToolsForModel, ToolWorkspace } from "./components/ToolWorkspace";
import { TopBar } from "./components/TopBar";
import { WorldOverview } from "./components/WorldOverview";
import { IntersectionDetailPanel } from "./components/IntersectionDetailPanel";
import { worldModels } from "./data/worldModels";
import { useSimulatedSnapshot } from "./hooks/useSimulatedSnapshot";
import { useWorld } from "./hooks/useWorld";
import { AgentNode, SelectionRef } from "./types";
import type { WorldView } from "./api/agentNetworkClient";

// 发现的 model_id → 前端展示（人类标签 + TopBar 用的富 worldModel 上下文）。
// 新增 model 在此登记即可；未登记的用 model_id 作标签。
const MODEL_LABEL: Record<string, string> = { "traffic-control": "交通信号管控" };
const MODEL_TO_RICH: Record<string, string> = { "traffic-control": "wm-smart-signal" };

function nodeCommandTypes(node: AgentNode): string[] {
  const metrics = node.metrics as Record<string, unknown>;
  const raw = metrics.commandTypes ?? metrics.command_types;
  return Array.isArray(raw) ? raw.map(String).filter(Boolean) : [];
}

function nodeHasCommands(node: AgentNode): boolean {
  return nodeCommandTypes(node).length > 0;
}

function pickCommandTarget(nodes: AgentNode[], selectedModelId: string, world: WorldView | null): AgentNode | null {
  const modelMembers = new Set(world?.models.find((m) => m.modelId === selectedModelId)?.members ?? []);
  const commandNodes = nodes.filter((node) => node.nodeType === "agent" && nodeHasCommands(node));
  const scopedNodes = modelMembers.size ? commandNodes.filter((node) => modelMembers.has(node.id)) : commandNodes;
  const candidates = scopedNodes.length ? scopedNodes : commandNodes;
  return (
    candidates.find((node) => node.status === "online" && nodeCommandTypes(node).includes("control_signal_inference")) ??
    candidates.find((node) => nodeCommandTypes(node).includes("control_signal_inference")) ??
    candidates.find((node) => node.status === "online") ??
    candidates[0] ??
    null
  );
}

export default function App() {
  const { snapshot, runtime, source } = useSimulatedSnapshot();
  // 仅当 ?source=gateway 且网关返回有效节点时为真（否则回落 mock，见 useSimulatedSnapshot）。
  const gatewayMode = source === "gateway";

  // 统一世界（/world）+ 视图：world=世界总览，model=成员地图（控制台为同图叠加的右侧命令闭环列）。
  const world = useWorld(gatewayMode);
  const [activeView, setActiveView] = useState<"world" | "model">("world");
  const [selectedModelId, setSelectedModelId] = useState("");
  const [controlOpen, setControlOpen] = useState(false);

  // TopBar / mock 沿用既有 worldModels。
  const [activeWorldModelId, setActiveWorldModelId] = useState(worldModels[0]?.id ?? "");
  const [activeToolId, setActiveToolId] = useState("");
  const activeWorldModel = worldModels.find((model) => model.id === activeWorldModelId) ?? worldModels[0];
  const activeTools = useMemo(() => (activeWorldModel ? getToolsForModel(activeWorldModel) : []), [activeWorldModel]);

  useEffect(() => {
    setActiveToolId(activeTools[0]?.id ?? "");
  }, [activeWorldModel?.id, activeTools]);

  // 左栏列表：gateway = 自发现 model（/world）；mock = 写死 worldModels。
  const gatewayItems: ToolbarItem[] = useMemo(
    () =>
      (world?.models ?? []).map((m) => ({
        id: m.modelId,
        name: MODEL_LABEL[m.modelId] ?? m.modelId,
        subtitle: `${m.members.length} 成员 · ${m.status}`,
        status: m.status
      })),
    [world]
  );
  const mockItems: ToolbarItem[] = worldModels.map((m) => ({ id: m.id, name: m.name, subtitle: m.category }));

  const selectDiscoveredModel = (mid: string) => {
    setSelectedModelId(mid);
    const rich = MODEL_TO_RICH[mid];
    if (rich) setActiveWorldModelId(rich);
    setControlOpen(false);
    setActiveView("model");
  };

  // 控制台（右侧命令闭环列）选中节点 + 收起态。
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => window.matchMedia("(max-width: 1180px)").matches);

  const inspectorOn = activeView === "model" && controlOpen;
  // per-junction agent（traffic-*-sv-j<jid>）选中 → 显示该路口 World Status 详情（P-10 IntersectionDetailPanel）。
  const junctionSelected = Boolean(selectedId && /-sv-j/i.test(selectedId));

  // 控制台打开但未选节点时，默认选第一个可下发命令的成员。
  useEffect(() => {
    if (!inspectorOn) return;
    const current = selectedId ? snapshot.nodes.find((n) => n.id === selectedId) : null;
    if (current && nodeHasCommands(current)) return;
    const target = pickCommandTarget(snapshot.nodes, selectedModelId, world);
    setSelectedId(target?.id ?? null);
  }, [inspectorOn, selectedId, selectedModelId, snapshot.nodes, world]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 1180px)");
    const sync = () => setInspectorCollapsed(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const selected = useMemo<SelectionRef | null>(
    () => (selectedId ? { kind: "node", id: selectedId } : null),
    [selectedId]
  );

  if (gatewayMode) {
    return (
      <main className={`app-shell gateway-shell${inspectorOn || junctionSelected ? "" : " no-inspector"}`}>
        <LeftToolbar
          items={gatewayItems}
          activeId={selectedModelId}
          onSelect={selectDiscoveredModel}
          onSelectWorld={() => {
            setActiveView("world");
            setControlOpen(false);
          }}
          worldActive={activeView === "world"}
        />
        <section className="workspace gateway-workspace">
          <TopBar activeWorldModel={activeWorldModel} runtime={runtime} />
          <div className="gateway-status-strip" aria-label="数据源状态">
            <span className="gateway-source-badge live">source gateway</span>
            <span>agent {world?.agents.length ?? 0}</span>
            <span>model {world?.models.length ?? 0}</span>
            <span>健康 {snapshot.summary.healthyPercent}%</span>
            <span>Kafka lag {snapshot.summary.kafkaLagMs} ms</span>
            <span>{new Date(snapshot.generatedAt).toLocaleTimeString()}</span>
          </div>
          <div className="stage-wrap gateway-stage">
            {activeView === "world" && <WorldOverview world={world} onSelectModel={selectDiscoveredModel} onAgentSelect={(id) => setSelectedId(id)} />}
            {activeView === "model" && (
              <WorldOverview
                world={world}
                focusModelId={selectedModelId}
                onAgentSelect={(id) => {
                  if (!controlOpen) setSelectedId(id);
                }}
                controlOpen={controlOpen}
                onToggleControl={() => setControlOpen((v) => !v)}
              />
            )}
          </div>
        </section>
        {junctionSelected ? (
          <IntersectionDetailPanel agentId={selectedId!} onClose={() => setSelectedId(null)} />
        ) : inspectorOn ? (
          <InspectorPanel
            snapshot={snapshot}
            selected={selected}
            source={source}
            worldModels={worldModels}
            collapsed={inspectorCollapsed}
            onCollapse={() => setInspectorCollapsed((value) => !value)}
          />
        ) : null}
      </main>
    );
  }

  // 默认：纯 mock 简化壳（与迁移前一致，未改动视觉）。
  return (
    <main className="app-shell simplified-shell">
      <LeftToolbar items={mockItems} activeId={activeWorldModelId} onSelect={setActiveWorldModelId} />
      <section className="workspace simplified-workspace">
        <TopBar activeWorldModel={activeWorldModel} runtime={runtime} />
        <div className="stage-wrap simplified-stage-wrap">
          {activeWorldModel && <ToolWorkspace model={activeWorldModel} snapshot={snapshot} tools={activeTools} activeToolId={activeToolId} runtime={runtime} />}
        </div>
      </section>
      <ModelIntroPanel model={activeWorldModel} runtime={runtime} tools={activeTools} activeToolId={activeToolId} onSelectTool={setActiveToolId} />
    </main>
  );
}
