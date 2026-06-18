import { ChevronRight, GitBranch, Video, Activity } from "lucide-react";
import { WorldModelDefinition } from "../types";

interface Props {
  worldModels: WorldModelDefinition[];
  activeWorldModelId: string;
  onSelectWorldModel: (id: string) => void;
}

function iconFor(model: WorldModelDefinition) {
  if (model.id === "wm-video-stream") return Video;
  if (model.id === "wm-junction-flow") return Activity;
  return GitBranch;
}

export function LeftToolbar({ worldModels, activeWorldModelId, onSelectWorldModel }: Props) {
  return (
    <aside className="left-rail slim-left-rail">
      <section className="rail-section world-model-only-section">
        <div className="section-title">
          <GitBranch size={18} />
          世界模型
        </div>
        <div className="entity-list world-model-list simplified">
          {worldModels.map((model) => {
            const Icon = iconFor(model);
            return (
              <button
                key={model.id}
                className={activeWorldModelId === model.id ? "entity-row world-model-row active" : "entity-row world-model-row"}
                onClick={() => onSelectWorldModel(model.id)}
              >
                <Icon size={18} />
                <span>
                  <b>{model.name}</b>
                  <small>{model.category}</small>
                </span>
                <ChevronRight size={16} />
              </button>
            );
          })}
        </div>
      </section>
    </aside>
  );
}