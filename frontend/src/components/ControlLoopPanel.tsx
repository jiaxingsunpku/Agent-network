import { Activity, CheckCircle2, RadioTower, ShieldCheck } from "lucide-react";
import { NetworkSnapshot, WorldModelDefinition } from "../types";

interface Props {
  snapshot: NetworkSnapshot;
  source: string;
  worldModel?: WorldModelDefinition;
  onSelectAgent: (agentId: string) => void;
  onSelectWorldModel: (worldModelId: string) => void;
}

function metricString(value: unknown, fallback = "-") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function stageClass(index: number, total: number, status?: string) {
  if (status === "warning") return "flow-stage warning";
  if (status === "paused") return "flow-stage pending";
  if (index < Math.max(1, total - 1)) return "flow-stage active";
  return status === "running" ? "flow-stage pending" : "flow-stage";
}

function firstBoundAgent(snapshot: NetworkSnapshot, model: WorldModelDefinition) {
  return snapshot.nodes.find((node) => model.boundNodeIds.includes(node.id)) ?? snapshot.nodes[0];
}

export function ControlLoopPanel({
  snapshot,
  source,
  worldModel,
  onSelectAgent,
  onSelectWorldModel
}: Props) {
  if (!worldModel) return null;
  const focusNode = firstBoundAgent(snapshot, worldModel);
  const stages = worldModel.stages.slice(0, 4);

  return (
    <section className="control-loop-panel world-model-flow-panel">
      <div className="agent-focus-list world-model-actions">
        <button
          className="virtual-agent-focus"
          type="button"
          onClick={() => onSelectWorldModel(worldModel.id)}
          title="查看世界模型详情"
        >
          <ShieldCheck size={17} />
          <span>{worldModel.category} · {worldModel.instanceName}</span>
        </button>
        {focusNode && (
          <button
            className="signalvision-agent-focus"
            type="button"
            onClick={() => onSelectAgent(focusNode.id)}
            title="查看绑定智能体"
          >
            <RadioTower size={17} />
            <span>{focusNode.label}</span>
          </button>
        )}
      </div>

      <div className="flow-stages" aria-label="世界模型执行流程">
        {stages.map((stage, index) => (
          <div className={stageClass(index, stages.length, worldModel.status)} key={stage.id}>
            {index === stages.length - 1 ? <CheckCircle2 size={16} /> : index === 0 ? <RadioTower size={16} /> : <Activity size={16} />}
            <span>
              <b>{stage.title}</b>
              <em>{stage.sourceSystem} · {metricString(stage.nodeIds?.length ?? stage.resourceTypes?.length, "-")}</em>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}