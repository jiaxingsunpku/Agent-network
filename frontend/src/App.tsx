import { useEffect, useMemo, useState } from "react";
import { LeftToolbar } from "./components/LeftToolbar";
import { ModelIntroPanel } from "./components/ModelIntroPanel";
import { InspectorPanel } from "./components/InspectorPanel";
import { getToolsForModel, ToolWorkspace } from "./components/ToolWorkspace";
import { TopBar } from "./components/TopBar";
import { VideoWorldModelView } from "./components/VideoWorldModelView";
import { worldModels } from "./data/worldModels";
import { useSimulatedSnapshot } from "./hooks/useSimulatedSnapshot";
import { AgentNode, SelectionRef } from "./types";

// 网关 agent 节点是否声明了可下发命令（metrics.commandTypes，见 docs/gateway-api.md §1.1）。
function nodeHasCommands(node: AgentNode): boolean {
  const metrics = node.metrics as Record<string, unknown>;
  const raw = metrics.commandTypes ?? metrics.command_types;
  return Array.isArray(raw) && raw.length > 0;
}

function MobileWorldModelSwitcher({
  activeWorldModelId,
  onSelectWorldModel
}: {
  activeWorldModelId: string;
  onSelectWorldModel: (id: string) => void;
}) {
  return (
    <div className="mobile-world-switcher" role="tablist" aria-label="世界模型快捷切换">
      {worldModels.map((model) => (
        <button
          key={model.id}
          type="button"
          role="tab"
          aria-selected={activeWorldModelId === model.id}
          className={activeWorldModelId === model.id ? "active" : ""}
          onClick={() => onSelectWorldModel(model.id)}
        >
          {model.name}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const { snapshot, runtime, source } = useSimulatedSnapshot();
  // 仅当 ?source=gateway 且网关返回有效节点时为真（否则回落 mock，见 useSimulatedSnapshot）。
  const gatewayMode = source === "gateway";

  const initialWorldModelId = worldModels[0]?.id ?? "";
  const [activeWorldModelId, setActiveWorldModelId] = useState(initialWorldModelId);
  const [activeToolId, setActiveToolId] = useState("");
  const activeWorldModel = worldModels.find((model) => model.id === activeWorldModelId) ?? worldModels[0];
  const showVideoQA = activeWorldModel?.id === "wm-video-stream";
  const showGatewayInspector = activeWorldModel?.id !== "wm-video-stream";
  const activeTools = useMemo(() => (activeWorldModel ? getToolsForModel(activeWorldModel) : []), [activeWorldModel]);

  useEffect(() => {
    setActiveToolId(activeTools[0]?.id ?? "");
  }, [activeWorldModel?.id, activeTools]);

  // 网关模式：选中节点 + Inspector 收起态。
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(() => window.matchMedia("(max-width: 1180px)").matches);

  // 节点排序：执行体/智能体优先，再路口，网关服务节点末尾。
  const gatewayNodes = useMemo(() => {
    const order: Record<string, number> = { agent: 0, region: 1, service: 2 };
    return [...snapshot.nodes].sort((a, b) => (order[a.nodeType] ?? 9) - (order[b.nodeType] ?? 9));
  }, [snapshot.nodes]);

  // 默认选中第一个可下发命令的执行体（如 traffic-virtual-001），便于直接演示命令闭环。
  useEffect(() => {
    if (!gatewayMode) return;
    if (selectedId && snapshot.nodes.some((n) => n.id === selectedId)) return;
    const target = snapshot.nodes.find((n) => n.nodeType === "agent" && nodeHasCommands(n)) ?? snapshot.nodes[0];
    setSelectedId(target?.id ?? null);
  }, [gatewayMode, snapshot.nodes, selectedId]);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 1180px)");
    const sync = () => setInspectorCollapsed(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  // selected 引用必须稳定：App 每秒重渲（runtime 本地推演），若每帧新建对象字面量，
  // InspectorPanel 的 projection 副作用（依赖 [selected]）会每秒重跑、先 setProjection(null)
  // 再重拉，导致右栏在 ProjectionPanel ⇄ NodePanel 间每秒闪烁。memo 到 selectedId 即可。
  const selected = useMemo<SelectionRef | null>(
    () => (selectedId ? { kind: "node", id: selectedId } : null),
    [selectedId]
  );

  if (gatewayMode) {
    return (
      <main className={`app-shell gateway-shell${showGatewayInspector ? "" : " no-inspector"}`}>
        <LeftToolbar
          worldModels={worldModels}
          activeWorldModelId={activeWorldModel?.id ?? activeWorldModelId}
          onSelectWorldModel={setActiveWorldModelId}
        />
        <section className="workspace gateway-workspace">
          <TopBar activeWorldModel={activeWorldModel} runtime={runtime} />
          <MobileWorldModelSwitcher
            activeWorldModelId={activeWorldModel?.id ?? activeWorldModelId}
            onSelectWorldModel={setActiveWorldModelId}
          />
          <div className="gateway-status-strip" aria-label="数据源状态">
            <span className="gateway-source-badge live">source gateway</span>
            <span>节点 {snapshot.nodes.length}</span>
            <span>智能体 {snapshot.summary.agents}</span>
            <span>健康 {snapshot.summary.healthyPercent}%</span>
            <span>更新 {snapshot.summary.updateRate}/s</span>
            <span>Kafka lag {snapshot.summary.kafkaLagMs} ms</span>
            <span>{new Date(snapshot.generatedAt).toLocaleTimeString()}</span>
          </div>
          {showGatewayInspector && (
            <div className="gateway-node-picker" role="tablist" aria-label="网关节点">
              {gatewayNodes.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  role="tab"
                  aria-selected={selectedId === node.id}
                  className={`gateway-node-btn type-${node.nodeType}${selectedId === node.id ? " active" : ""}`}
                  onClick={() => {
                    setSelectedId(node.id);
                    setInspectorCollapsed(false);
                  }}
                  title={node.id}
                >
                  <i className={`node-dot ${node.status}`} />
                  <span>{node.label}</span>
                </button>
              ))}
            </div>
          )}
          {/* 视频世界模型：去 mock 化，问答升监控主界面 + 协作任务侧栏（P9）；
              其余模型保留功能模块工具条 + ToolWorkspace。 */}
          {!showVideoQA && (
            <div className="gateway-tool-tabs" role="tablist" aria-label="功能模块">
              {activeTools.map((tool) => {
                const Icon = tool.icon;
                return (
                <button
                  key={tool.id}
                  type="button"
                  role="tab"
                  aria-selected={activeToolId === tool.id}
                  className={activeToolId === tool.id ? "gateway-tool-tab active" : "gateway-tool-tab"}
                  onClick={() => setActiveToolId(tool.id)}
                  title={tool.description}
                >
                  <Icon size={16} />
                  <span>{tool.title}</span>
                </button>
                );
              })}
            </div>
          )}
          <div className={`stage-wrap gateway-stage${showVideoQA ? " video-stage" : ""}`}>
            {showVideoQA ? (
              <VideoWorldModelView />
            ) : (
              activeWorldModel && (
                <ToolWorkspace
                  model={activeWorldModel}
                  snapshot={snapshot}
                  tools={activeTools}
                  activeToolId={activeToolId}
                  runtime={runtime}
                />
              )
            )}
          </div>
        </section>
        {showGatewayInspector && (
          <InspectorPanel
            snapshot={snapshot}
            selected={selected}
            source={source}
            worldModels={worldModels}
            collapsed={inspectorCollapsed}
            onCollapse={() => setInspectorCollapsed((value) => !value)}
          />
        )}
      </main>
    );
  }

  // 默认：纯 mock 简化壳（与迁移前一致，未改动视觉）。
  return (
    <main className="app-shell simplified-shell">
      <LeftToolbar
        worldModels={worldModels}
        activeWorldModelId={activeWorldModel?.id ?? activeWorldModelId}
        onSelectWorldModel={setActiveWorldModelId}
      />
      <section className="workspace simplified-workspace">
        <TopBar activeWorldModel={activeWorldModel} runtime={runtime} />
        <MobileWorldModelSwitcher
          activeWorldModelId={activeWorldModel?.id ?? activeWorldModelId}
          onSelectWorldModel={setActiveWorldModelId}
        />
        <div className="stage-wrap simplified-stage-wrap">
          {activeWorldModel && <ToolWorkspace model={activeWorldModel} snapshot={snapshot} tools={activeTools} activeToolId={activeToolId} runtime={runtime} />}
        </div>
      </section>
      <ModelIntroPanel model={activeWorldModel} runtime={runtime} tools={activeTools} activeToolId={activeToolId} onSelectTool={setActiveToolId} />
    </main>
  );
}
