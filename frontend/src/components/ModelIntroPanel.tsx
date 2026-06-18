import { Info } from "lucide-react";
import { WorldModelDefinition, WorldModelRuntime } from "../types";
import { ToolDef } from "./ToolWorkspace";

interface Props {
  model?: WorldModelDefinition;
  runtime?: WorldModelRuntime;
  tools?: ToolDef[];
  activeToolId?: string;
  onSelectTool?: (toolId: string) => void;
}

function metricValue(value: unknown) {
  if (typeof value === "number") return value.toLocaleString("zh-CN");
  return String(value);
}

function metricsFor(model: WorldModelDefinition, runtime?: WorldModelRuntime): Array<[string, unknown]> {
  if (!runtime) return Object.entries(model.metrics).slice(0, 4);
  if (model.id === "wm-video-stream") {
    return [["摄像头", runtime.video.onlineCameras], ["检测任务", runtime.video.detectionTasks], ["事件", runtime.video.eventCount], ["延迟", `${runtime.video.latencyMs}ms`]];
  }
  if (model.id === "wm-junction-flow") {
    return [["总流量", runtime.traffic.totalFlow], ["平均速度", `${runtime.traffic.avgSpeedKmh}km/h`], ["平均延误", `${runtime.traffic.avgDelaySec}s`], ["事件", runtime.traffic.incidents]];
  }
  return [["信号路口", runtime.traffic.activeSignals], ["平均延误", `${runtime.traffic.avgDelaySec}s`], ["仿真时间", runtime.simTime], ["任务", runtime.status.runningJobs]];
}

export function ModelIntroPanel({ model, runtime, tools = [], activeToolId, onSelectTool }: Props) {
  if (!model) return null;
  const metrics = metricsFor(model, runtime);

  return (
    <aside className="simple-intro-panel">
      <div className="intro-header">
        <Info size={18} />
        <span>世界模型</span>
      </div>
      <h2>{model.name}</h2>
      <p>{model.description}</p>

      {metrics.length > 0 && (
        <div className="intro-overview">
          <span className="intro-overview-title">总览</span>
          <div className="intro-metric-grid">
            {metrics.map(([label, value]) => (
              <div className="intro-metric" key={label}>
                <b>{metricValue(value)}</b>
                <span>{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="intro-tool-list" aria-label="功能模块">
        <span className="intro-tool-list-title">功能模块</span>
        {tools.map((tool) => {
          const Icon = tool.icon;
          return (
            <button
              key={tool.id}
              className={activeToolId === tool.id ? "intro-tool-button active" : "intro-tool-button"}
              onClick={() => onSelectTool?.(tool.id)}
            >
              <Icon size={20} />
              <b>{tool.title}</b>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
