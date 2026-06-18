export type ViewMode = "workflow" | "map" | "network3d";
export type NodeStatus = "online" | "warning" | "offline" | "syncing";
export type SelectionKind = "world_model" | "node" | "edge" | "resource";

export interface MetricMap {
  [key: string]: unknown;
}

export type AgentNetworkCommandType =
  | "set_signal_plan"
  | "set_observation_rate"
  | "enter_maintenance_demo"
  | "sv.inference.start"
  | "sv.inference.stop"
  | "sv.inference.status"
  | "sv.inference.snapshot";

export interface AgentNode {
  id: string;
  label: string;
  nodeType: "agent" | "region" | "service";
  group: string;
  position: { x: number; y: number };
  status: NodeStatus;
  health: number;
  tags: string[];
  metrics: MetricMap;
}

export interface AgentEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  directed: boolean;
  relationType: string;
  status: NodeStatus;
  metrics: MetricMap;
}

export interface PhysicalResource {
  id: string;
  label: string;
  resourceType: "camera" | "database" | "detector" | "simulator" | "storage" | "controller";
  anchorAgentId: string;
  height: number;
  direction: "input" | "output" | "bidirectional";
  status: NodeStatus;
  metrics: MetricMap;
}

export interface TimelinePoint {
  t: number;
  value: number;
}

export interface NetworkEvent {
  id: string;
  severity: "info" | "warning" | "critical";
  title: string;
  targetId: string;
  time: string;
}

export interface NetworkSnapshot {
  version: string;
  generatedAt: string;
  topologyVersion: string;
  region: string;
  summary: {
    agents: number;
    relations: number;
    resources: number;
    healthyPercent: number;
    kafkaLagMs: number;
    updateRate: number;
  };
  nodes: AgentNode[];
  edges: AgentEdge[];
  resources: PhysicalResource[];
  trend: TimelinePoint[];
  events: NetworkEvent[];
}

export type WorldModelStatus = "ready" | "running" | "warning" | "paused";
export type WorldModelActionKind = "inference" | "training" | "continual_learning" | "control" | "report";

export interface WorldModelAction {
  id: string;
  label: string;
  kind: WorldModelActionKind;
  description: string;
  sourceSystem: string;
  buttonLabel: string;
  resultTitle: string;
  resultMetrics: MetricMap;
}

export interface WorldModelStage {
  id: string;
  title: string;
  description: string;
  sourceSystem: string;
  nodeIds?: string[];
  resourceTypes?: PhysicalResource["resourceType"][];
}

export interface WorldModelReference {
  copiedFrom: string[];
  notes: string;
  training?: MetricMap;
  continualLearning?: MetricMap;
}

export interface WorldModelDefinition {
  id: string;
  name: string;
  subtitle: string;
  category: string;
  status: WorldModelStatus;
  templateName: string;
  instanceName: string;
  objective: string;
  description: string;
  boundNodeIds: string[];
  boundResourceTypes: PhysicalResource["resourceType"][];
  tags: string[];
  stages: WorldModelStage[];
  actions: WorldModelAction[];
  metrics: MetricMap;
  outputs: string[];
  reference: WorldModelReference;
}


export interface RuntimeEvent {
  id: string;
  severity: "info" | "warning" | "critical";
  title: string;
  target: string;
  time: string;
}

export interface HotIntersectionRuntime {
  id: string;
  label: string;
  flow: number;
  speedKmh: number;
  delaySec: number;
  queueM: number;
  state: "畅通" | "缓行" | "拥堵" | "严重";
}

export interface WorldModelRuntime {
  generatedAt: string;
  frame: number;
  simTime: string;
  mode: "sumo_stream";
  status: {
    label: string;
    pipeline: string;
    latencyMs: number;
    tickMs: number;
    recordsPerMin: number;
    runningJobs: number;
  };
  traffic: {
    totalFlow: number;
    avgSpeedKmh: number;
    avgDelaySec: number;
    maxQueueM: number;
    congestionIndex: number;
    activeSignals: number;
    processedRecords: number;
    incidents: number;
  };
  training: {
    jobName: string;
    progress: number;
    episode: number;
    reward: number;
    loss: number;
    etaMin: number;
    status: string;
  };
  video: {
    onlineCameras: number;
    detectionTasks: number;
    eventCount: number;
    latencyMs: number;
  };
  chart: number[];
  hotIntersections: HotIntersectionRuntime[];
  events: RuntimeEvent[];
}

export interface SelectionRef {
  kind: SelectionKind;
  id: string;
}