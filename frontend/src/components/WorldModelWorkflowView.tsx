import { Activity, ArrowRight, Bot, Box, Database, Gauge, RadioTower, Send, ShieldCheck } from "lucide-react";
import {
  AgentNode,
  NetworkSnapshot,
  PhysicalResource,
  SelectionRef,
  WorldModelAction,
  WorldModelDefinition,
  WorldModelStage
} from "../types";

interface Props {
  snapshot: NetworkSnapshot;
  model?: WorldModelDefinition;
  selected: SelectionRef | null;
  search: string;
  onSelect: (selection: SelectionRef | null) => void;
}

function statusLabel(status?: string) {
  if (status === "running") return "运行中";
  if (status === "ready") return "待命";
  if (status === "warning") return "告警";
  if (status === "paused") return "暂停";
  return "就绪";
}

function statusClass(status?: string) {
  if (status === "warning") return "warning";
  if (status === "paused") return "offline";
  if (status === "running") return "syncing";
  return "online";
}

function formatMetricValue(value: unknown) {
  if (typeof value === "number") {
    if (value > 0 && value < 1) return value.toFixed(2);
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(1);
  }
  return String(value);
}

function modelNodes(snapshot: NetworkSnapshot, model: WorldModelDefinition) {
  const nodes = snapshot.nodes.filter((node) => model.boundNodeIds.includes(node.id));
  return nodes.length ? nodes : snapshot.nodes.slice(0, 6);
}

function nodesForStage(snapshot: NetworkSnapshot, model: WorldModelDefinition, stage: WorldModelStage) {
  const ids = stage.nodeIds?.length ? stage.nodeIds : model.boundNodeIds;
  const nodes = snapshot.nodes.filter((node) => ids.includes(node.id));
  return nodes.slice(0, 4);
}

function resourcesForStage(snapshot: NetworkSnapshot, model: WorldModelDefinition, stage: WorldModelStage) {
  const nodeIds = new Set((stage.nodeIds?.length ? stage.nodeIds : model.boundNodeIds));
  return snapshot.resources.filter((resource) => {
    const typeMatch = stage.resourceTypes?.includes(resource.resourceType) || model.boundResourceTypes.includes(resource.resourceType);
    return typeMatch && (nodeIds.has(resource.anchorAgentId) || Boolean(stage.resourceTypes?.includes(resource.resourceType)));
  }).slice(0, 4);
}

function actionIcon(action: WorldModelAction) {
  if (action.kind === "training") return <Gauge size={16} />;
  if (action.kind === "continual_learning") return <Activity size={16} />;
  if (action.kind === "control") return <ShieldCheck size={16} />;
  if (action.kind === "report") return <Database size={16} />;
  return <Send size={16} />;
}

function highlight(text: string, query: string) {
  if (!query) return false;
  return text.toLowerCase().includes(query.toLowerCase());
}

function NodeChip({ node, selected, onSelect }: { node: AgentNode; selected: SelectionRef | null; onSelect: (selection: SelectionRef) => void }) {
  return (
    <button
      className={selected?.kind === "node" && selected.id === node.id ? "workflow-chip node active" : "workflow-chip node"}
      onClick={() => onSelect({ kind: "node", id: node.id })}
      title={`${node.id} · ${node.group}`}
    >
      <Bot size={14} />
      <span>{node.label}</span>
      <i className={`status-dot ${node.status}`} />
    </button>
  );
}

function ResourceChip({ resource, selected, onSelect }: { resource: PhysicalResource; selected: SelectionRef | null; onSelect: (selection: SelectionRef) => void }) {
  return (
    <button
      className={selected?.kind === "resource" && selected.id === resource.id ? "workflow-chip resource active" : "workflow-chip resource"}
      onClick={() => onSelect({ kind: "resource", id: resource.id })}
      title={`${resource.resourceType} · ${resource.direction}`}
    >
      <RadioTower size={14} />
      <span>{resource.label}</span>
    </button>
  );
}

export function WorldModelWorkflowView({ snapshot, model, selected, search, onSelect }: Props) {
  if (!model) {
    return (
      <section className="workflow-stage-view empty">
        <div className="workflow-empty-card">暂无可用世界模型</div>
      </section>
    );
  }

  const query = search.trim();
  const boundNodes = modelNodes(snapshot, model);
  const activeNodeCount = boundNodes.filter((node) => node.status !== "offline").length;
  const primaryMetrics = Object.entries(model.metrics).slice(0, 4);

  return (
    <section className="workflow-stage-view">
      <div className="workflow-hero-panel">
        <div>
          <span className="workflow-kicker">世界模型工作流</span>
          <h2>{model.name}</h2>
          <p>{model.objective}</p>
        </div>
        <div className="workflow-hero-status">
          <span className={`status-badge ${statusClass(model.status)}`}><i />{statusLabel(model.status)}</span>
          <button className="model-open-btn hero" onClick={() => onSelect({ kind: "world_model", id: model.id })}>
            打开模型详情
            <ArrowRight size={16} />
          </button>
        </div>
      </div>

      <div className="workflow-summary-band">
        <div>
          <strong>{boundNodes.length}</strong>
          <span>绑定智能体</span>
        </div>
        <div>
          <strong>{activeNodeCount}</strong>
          <span>在线协作节点</span>
        </div>
        <div>
          <strong>{model.stages.length}</strong>
          <span>工作流阶段</span>
        </div>
        <div>
          <strong>{model.actions.length}</strong>
          <span>可执行动作</span>
        </div>
        {primaryMetrics.map(([key, value]) => (
          <div key={key}>
            <strong>{formatMetricValue(value)}</strong>
            <span>{key}</span>
          </div>
        ))}
      </div>

      <div className="workflow-board-shell">
        <div className="workflow-board-header">
          <div>
            <h3>多智能体执行链路</h3>
            <p>从输入资源开始，经由智能体协作与聚合，最后形成任务输出。地图仅作为空间子视图。</p>
          </div>
          <div className="workflow-template-pill">
            <Box size={15} />
            {model.templateName}
          </div>
        </div>

        <div className="workflow-board">
          {model.stages.map((stage, index) => {
            const stageNodes = nodesForStage(snapshot, model, stage);
            const stageResources = resourcesForStage(snapshot, model, stage);
            const matched = highlight(`${stage.title} ${stage.description} ${stage.sourceSystem}`, query);
            return (
              <div className={matched ? "workflow-column matched" : "workflow-column"} key={stage.id}>
                <div className="workflow-column-card" onClick={() => onSelect({ kind: "world_model", id: model.id })}>
                  <div className="workflow-column-index">{index + 1}</div>
                  <div className="workflow-column-title">
                    <h4>{stage.title}</h4>
                    <span>{stage.sourceSystem}</span>
                  </div>
                  <p>{stage.description}</p>

                  <div className="workflow-chip-group">
                    <b>智能体</b>
                    {stageNodes.length ? stageNodes.map((node) => (
                      <NodeChip key={node.id} node={node} selected={selected} onSelect={onSelect} />
                    )) : <span className="workflow-placeholder">由世界模型调度</span>}
                  </div>

                  <div className="workflow-chip-group">
                    <b>数据/资源</b>
                    {stageResources.length ? stageResources.map((resource) => (
                      <ResourceChip key={resource.id} resource={resource} selected={selected} onSelect={onSelect} />
                    )) : <span className="workflow-placeholder">无直接资源绑定</span>}
                  </div>
                </div>
                {index < model.stages.length - 1 && (
                  <div className="workflow-arrow" aria-hidden="true">
                    <ArrowRight size={24} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="workflow-bottom-grid">
        <div className="workflow-output-panel">
          <div className="card-title">
            <Database size={18} />
            输出与制品
          </div>
          <div className="io-list">
            {model.outputs.map((output) => <span key={output}>{output}</span>)}
          </div>
        </div>
        <div className="workflow-action-panel">
          <div className="card-title">
            <Send size={18} />
            任务动作
          </div>
          <div className="workflow-action-row">
            {model.actions.map((action) => (
              <button key={action.id} className="workflow-action-mini" onClick={() => onSelect({ kind: "world_model", id: model.id })}>
                {actionIcon(action)}
                <span>
                  <b>{action.buttonLabel}</b>
                  <small>{action.label}</small>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}