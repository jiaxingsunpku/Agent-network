import { WorldModelDefinition } from "../types";

const trafficFocusNodes = [
  "gg-xinzhu-minzu",
  "gg-xiongchu-minzu",
  "gg-xiongchu-xiongzhuang",
  "gg-jiayuan-chuangye",
  "gg-xiongchu-jiayuan",
  "gg-gaoxin-guanggu1",
  "gg-guanggu6xiao"
];

const trafficSources = [
  "/home/sjx/project/rebuild-with-inf-back/cache/output/netdata.pkl",
  "/home/sjx/project/地图车流数据/武汉交通数据/7个路口流量数据汇总-20250721",
  "/home/sjx/project/地图车流数据/武汉交通数据/1_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/2_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/3_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/4_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/5_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/6_processed.xlsx",
  "/home/sjx/project/地图车流数据/武汉交通数据/7_processed.xlsx"
];

export const worldModels: WorldModelDefinition[] = [
  {
    id: "wm-smart-signal",
    name: "智能信号灯世界模型",
    subtitle: "最大路网 · 信号控制 · 实时态势",
    category: "交通控制",
    status: "running",
    templateName: "smart-signal-shell",
    instanceName: "最大 output 路网演示实例",
    objective: "以最大 output 路网为主展示底图，接入 SignalVision 做信号控制推理（启停/切换控制算法）与路口态势监控。",
    description: "面向智能信号灯控制的世界模型：路网态势 + SignalVision 信号控制推理闭环。ANP 只下发粗粒度控制命令（开始/切换推理），不做模型训练（训练属 SignalTrain，独立系统）。",
    boundNodeIds: trafficFocusNodes,
    boundResourceTypes: ["detector", "database", "controller", "simulator", "storage"],
    tags: ["traffic", "signal", "inference", "control"],
    metrics: {
      信号路口: 207,
      道路边: 40050,
      车道: 48657,
      关注路口: 7
    },
    outputs: ["交通地图", "信号控制建议", "控制推理状态", "路口态势"],
    stages: [
      { id: "map", title: "路网展示", description: "最大 output 路网 Canvas 展示。", sourceSystem: "rebuild / SignalVision visualization", nodeIds: trafficFocusNodes },
      { id: "control", title: "信号控制推理", description: "SignalVision 启停/切换控制算法（maxpressure/colight…），真驱动仿真。", sourceSystem: "SignalVision /api/simulation" }
    ],
    actions: [
      { id: "signal-map", label: "交通地图", kind: "report", description: "显示最大交通路网。", sourceSystem: "rebuild visualization", buttonLabel: "地图", resultTitle: "最大交通路网", resultMetrics: { 信号路口: 207, 道路边: 40050, 车道: 48657 } },
      { id: "signal-control", label: "信号控制推理", kind: "inference", description: "下发控制推理命令（开始/切换算法）。", sourceSystem: "SignalVision control inference", buttonLabel: "控制", resultTitle: "控制推理", resultMetrics: { 算法: "maxpressure / colight", 命令: "control_signal_inference" } }
    ],
    reference: {
      copiedFrom: trafficSources,
      notes: "ANP 接入 SignalVision（推理/控制）：信号控制推理 + 路口态势；模型训练（SignalTrain）不在本世界模型内。"
    }
  },
  {
    id: "wm-video-stream",
    name: "监控视频流处理世界模型",
    subtitle: "视频接入 · 目标检测 · 语义事件",
    category: "视频处理",
    status: "ready",
    templateName: "vision-stream-shell",
    instanceName: "VisionHub 实时感知实例",
    objective: "汇聚监控视频流、边缘检测结果和语义事件摘要，支撑交通态势感知与事件发布。",
    description: "该世界模型统一管理摄像头视频流接入、目标检测、事件摘要和语义结果发布。",
    boundNodeIds: [],
    boundResourceTypes: ["camera", "database", "storage"],
    tags: ["vision", "video", "edge", "streaming"],
    metrics: {
      状态: "实时同步",
      摄像头: 24,
      检测流: 12
    },
    outputs: ["视频流状态", "目标检测结果", "事件摘要"],
    stages: [
      { id: "video-ingest", title: "视频流接入", description: "接入 VisionHub / Kafka 视频流。", sourceSystem: "VisionHub streaming" },
      { id: "detect", title: "目标检测", description: "汇聚边缘智能体语义检测结果。", sourceSystem: "edge agents" }
    ],
    actions: [],
    reference: {
      copiedFrom: ["/home/sjx/worldmodel/agent-network-kafka-test/captured"],
      notes: "系统已完成视频流接入、检测、事件摘要与结果发布链路。"
    }
  },
  {
    id: "wm-junction-flow",
    name: "路口流量监控世界模型",
    subtitle: "流量数据 · 实时监控 · 路口指标",
    category: "流量监控",
    status: "running",
    templateName: "junction-flow-shell",
    instanceName: "武汉光谷 7 路口流量实例",
    objective: "围绕武汉光谷 7 个真实路口流量数据，展示路口流量、速度、排队和延误指标监控。",
    description: "面向路口流量监控与统计分析的世界模型，汇聚实时交通数据、数据批次、路口指标和交通地图。",
    boundNodeIds: trafficFocusNodes,
    boundResourceTypes: ["detector", "database", "storage"],
    tags: ["traffic", "flow", "monitoring", "wuhan"],
    metrics: {
      关注路口: 7,
      聚合记录: 6602,
      总流量: 360859,
      平均速度kmh: 30,
      平均延误s: 35
    },
    outputs: ["实时交通数据", "路口指标", "数据批次", "监控摘要"],
    stages: [
      { id: "flow-ingest", title: "流量接入", description: "读取真实路口处理表。", sourceSystem: "武汉交通数据", nodeIds: trafficFocusNodes, resourceTypes: ["detector"] },
      { id: "flow-summary", title: "指标汇总", description: "按路口统计流量、速度、排队和延误。", sourceSystem: "worldmodel summary" }
    ],
    actions: [
      { id: "flow-monitor", label: "实时交通数据", kind: "report", description: "打开流量监控面板。", sourceSystem: "SignalVision realtime traffic data", buttonLabel: "监控", resultTitle: "路口流量监控", resultMetrics: { 聚合记录: 6602, 总流量: 360859 } }
    ],
    reference: {
      copiedFrom: trafficSources,
      notes: "使用真实处理表字段和汇总值，作为流量监控演示数据。"
    }
  }
];