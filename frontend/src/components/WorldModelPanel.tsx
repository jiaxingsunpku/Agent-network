import { useEffect, useState } from "react";
import { Activity, CheckCircle2, Database, RadioTower, Send, ShieldCheck } from "lucide-react";
import { NetworkSnapshot, WorldModelAction, WorldModelDefinition } from "../types";

interface Props {
  model: WorldModelDefinition;
  snapshot: NetworkSnapshot;
  source: string;
}

function formatValue(value: unknown) {
  if (typeof value === "number") {
    if (value > 0 && value < 1) return value.toFixed(2);
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(2);
  }
  return String(value);
}

function MetricGrid({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics);
  if (!entries.length) return <p className="empty-hint">暂无指标</p>;
  return (
    <div className="metric-list">
      {entries.map(([key, value]) => (
        <div className="metric-item" key={key}>
          <span>{key}</span>
          <b>{formatValue(value)}</b>
        </div>
      ))}
    </div>
  );
}

function statusText(status: WorldModelDefinition["status"]) {
  if (status === "running") return "运行中";
  if (status === "ready") return "待命";
  if (status === "warning") return "告警";
  return "暂停";
}

function actionHint(action: WorldModelAction) {
  if (action.kind === "training") return "套用 SignalTrain 训练工具字段，生成演示训练任务。";
  if (action.kind === "continual_learning") return "套用 SignalTrain 持续学习流程，展示批次、更新与推送。";
  if (action.kind === "control") return "只生成演示建议，不下发真实设备。";
  if (action.kind === "report") return "读取已复制的实验结果字段，展示对比报告。";
  return "读取边缘推理或 captured Kafka 结果，生成态势摘要。";
}

function actionLog(action: WorldModelAction, source: string) {
  const now = new Date().toLocaleTimeString();
  return [
    `[${now}] 选择动作：${action.label}`,
    `[${now}] 来源壳：${action.sourceSystem}`,
    `[${now}] 数据源：${source}`,
    `[${now}] 结果：${action.resultTitle}`
  ];
}

export function WorldModelPanel({ model, snapshot, source }: Props) {
  const [activeActionId, setActiveActionId] = useState<string | null>(null);
  const activeAction = model.actions.find((action) => action.id === activeActionId) ?? null;
  const boundNodes = snapshot.nodes.filter((node) => model.boundNodeIds.includes(node.id));
  const fallbackNodes = boundNodes.length ? boundNodes : snapshot.nodes.slice(0, 5);
  const boundNodeIds = new Set(fallbackNodes.map((node) => node.id));
  const boundResources = snapshot.resources.filter((resource) => (
    boundNodeIds.has(resource.anchorAgentId) || model.boundResourceTypes.includes(resource.resourceType)
  )).slice(0, 8);

  useEffect(() => {
    setActiveActionId(null);
  }, [model.id]);

  return (
    <>
      <div className="panel-card world-model-card">
        <div className="object-title-row">
          <h2>{model.name}</h2>
          <span className={`status-badge ${model.status === "warning" ? "warning" : model.status === "paused" ? "offline" : model.status === "running" ? "syncing" : "online"}`}>
            <i />{statusText(model.status)}
          </span>
        </div>
        <p className="panel-subtitle">{model.templateName} · {model.instanceName}</p>
        <p className="world-model-objective">{model.objective}</p>
        <MetricGrid metrics={model.metrics} />
      </div>

      <div className="panel-card">
        <div className="card-title">
          <Activity size={18} />
          工作流
        </div>
        <div className="workflow-list">
          {model.stages.map((stage, index) => (
            <div className="workflow-step" key={stage.id}>
              <i>{index + 1}</i>
              <span>
                <b>{stage.title}</b>
                <small>{stage.description}</small>
                <em>{stage.sourceSystem}</em>
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel-card">
        <div className="card-title">
          <Send size={18} />
          可执行动作
        </div>
        <div className="world-action-grid">
          {model.actions.map((action) => (
            <button
              key={action.id}
              className={activeActionId === action.id ? "world-action active" : "world-action"}
              type="button"
              onClick={() => setActiveActionId(action.id)}
              title={action.description}
            >
              <b>{action.buttonLabel}</b>
              <span>{action.label}</span>
            </button>
          ))}
        </div>
        {!activeAction && <p className="empty-hint">选择一个动作后展示演示结果；当前不会调用或运行参考系统。</p>}
        {activeAction && (
          <div className="action-result">
            <div className="projection-block-title">{activeAction.resultTitle}</div>
            <p className="muted-text">{actionHint(activeAction)}</p>
            <MetricGrid metrics={activeAction.resultMetrics} />
            <div className="action-log">
              {actionLog(activeAction, source).map((line) => <code key={line}>{line}</code>)}
            </div>
          </div>
        )}
      </div>

      <div className="panel-card">
        <div className="card-title">
          <RadioTower size={18} />
          绑定智能体
        </div>
        <div className="binding-list">
          {fallbackNodes.map((node) => (
            <div className="binding-row" key={node.id}>
              <i className={`status-dot ${node.status}`} />
              <span>
                <b>{node.label}</b>
                <small>{node.id} · {node.group}</small>
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="panel-card">
        <div className="card-title">
          <Database size={18} />
          输入输出与参考来源
        </div>
        <div className="io-list">
          {model.outputs.map((output) => <span key={output}>{output}</span>)}
        </div>
        <div className="binding-list reference-list">
          {model.reference.copiedFrom.map((path) => (
            <div className="binding-row" key={path}>
              <CheckCircle2 size={15} />
              <span>
                <b>{path}</b>
                <small>只读参考，未运行，未修改</small>
              </span>
            </div>
          ))}
        </div>
        <p className="muted-text"><ShieldCheck size={14} /> {model.reference.notes}</p>
      </div>

      {boundResources.length > 0 && (
        <div className="panel-card soft">
          <div className="card-title">
            <Database size={18} />
            物理/数据资源
          </div>
          <div className="resource-tags">
            {boundResources.map((resource) => (
              <span className="resource-chip readonly" key={resource.id}>{resource.label}</span>
            ))}
          </div>
        </div>
      )}
    </>
  );
}