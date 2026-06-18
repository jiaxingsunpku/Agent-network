import { GitBranch, Video, Activity } from "lucide-react";
import { WorldModelDefinition, WorldModelRuntime } from "../types";

interface Props {
  activeWorldModel?: WorldModelDefinition;
  runtime?: WorldModelRuntime;
}

function iconFor(model?: WorldModelDefinition) {
  if (model?.id === "wm-video-stream") return Video;
  if (model?.id === "wm-junction-flow") return Activity;
  return GitBranch;
}

export function TopBar({ activeWorldModel, runtime }: Props) {
  const Icon = iconFor(activeWorldModel);
  return (
    <header className="topbar simple-topbar">
      <div className="brand-block compact-brand">
        <div className="brand-mark"><Icon size={20} /></div>
        <h1>{activeWorldModel?.name ?? "世界模型工作台"}</h1>
      </div>
      {runtime && (
        <div className="runtime-status-chip" aria-label="运行状态">
          <i />
          <b>{runtime.status.label}</b>
          <span>{runtime.simTime}</span>
          <span>{runtime.status.latencyMs} ms</span>
        </div>
      )}
    </header>
  );
}
