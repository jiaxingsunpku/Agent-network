import { NetworkSnapshot } from "../types";

export const baseNetworkSnapshot: NetworkSnapshot = {
  version: "wuhan-guanggu-20250721-v1",
  generatedAt: new Date().toISOString(),
  topologyVersion: "output-netdata-40050-edge-v1",
  region: "最大 output 路网 · 武汉光谷关注路口",
  summary: {
    agents: 207,
    relations: 40050,
    resources: 48657,
    healthyPercent: 96,
    kafkaLagMs: 16,
    updateRate: 288
  },
  nodes: [
    {
      id: "gg-xinzhu-minzu",
      label: "新竹-民族",
      nodeType: "agent",
      group: "民族大道",
      position: { x: 150, y: 260 },
      status: "warning",
      health: 82,
      tags: ["wuhan", "guanggu", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "新竹路与民族大道", 数据行: 864, 方向数: 3, 时间点: 288, 总流量: 58131, 平均速度kmh: 23.5, 平均延误s: 53.0, 平均排队m: 33.7, 最大排队m: 75.7, 峰值时刻秒: 29700, 峰值流量: 319 }
    },
    {
      id: "gg-xiongchu-minzu",
      label: "雄楚-民族",
      nodeType: "agent",
      group: "民族大道",
      position: { x: 170, y: 470 },
      status: "warning",
      health: 84,
      tags: ["wuhan", "xiongchu", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "雄楚大道民族大道", 数据行: 1148, 方向数: 4, 时间点: 287, 总流量: 104637, 平均速度kmh: 29.9, 平均延误s: 35.2, 平均排队m: 51.1, 最大排队m: 110.9, 峰值时刻秒: 62699, 峰值流量: 517 }
    },
    {
      id: "gg-xiongchu-xiongzhuang",
      label: "雄楚-雄庄",
      nodeType: "agent",
      group: "雄楚大道",
      position: { x: 640, y: 470 },
      status: "warning",
      health: 78,
      tags: ["wuhan", "xiongchu", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "雄楚大道雄庄路", 数据行: 432, 方向数: 4, 时间点: 108, 总流量: 22413, 平均速度kmh: 26.8, 平均延误s: 41.2, 平均排队m: 51.9, 最大排队m: 114.3, 峰值时刻秒: 63297, 峰值流量: 944 }
    },
    {
      id: "gg-jiayuan-chuangye",
      label: "佳园-创业街",
      nodeType: "agent",
      group: "佳园路",
      position: { x: 380, y: 320 },
      status: "online",
      health: 92,
      tags: ["wuhan", "jiayuan", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "佳园路光谷创业街", 数据行: 1148, 方向数: 4, 时间点: 287, 总流量: 32853, 平均速度kmh: 35.1, 平均延误s: 22.3, 平均排队m: 39.2, 最大排队m: 144.5, 峰值时刻秒: 66000, 峰值流量: 236 }
    },
    {
      id: "gg-xiongchu-jiayuan",
      label: "雄楚-佳园",
      nodeType: "agent",
      group: "雄楚大道",
      position: { x: 410, y: 470 },
      status: "online",
      health: 90,
      tags: ["wuhan", "xiongchu", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "雄楚大道佳园路", 数据行: 1144, 方向数: 4, 时间点: 286, 总流量: 55019, 平均速度kmh: 32.8, 平均延误s: 28.0, 平均排队m: 33.3, 最大排队m: 128.3, 峰值时刻秒: 67200, 峰值流量: 381 }
    },
    {
      id: "gg-gaoxin-guanggu1",
      label: "高新-光谷一路",
      nodeType: "agent",
      group: "高新大道",
      position: { x: 560, y: 110 },
      status: "warning",
      health: 83,
      tags: ["wuhan", "gaoxin", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "高新大道光谷一路", 数据行: 1144, 方向数: 4, 时间点: 286, 总流量: 67908, 平均速度kmh: 29.9, 平均延误s: 38.7, 平均排队m: 39.4, 最大排队m: 114.3, 峰值时刻秒: 30000, 峰值流量: 463 }
    },
    {
      id: "gg-guanggu6xiao",
      label: "光谷六小",
      nodeType: "agent",
      group: "光谷片区",
      position: { x: 430, y: 175 },
      status: "online",
      health: 94,
      tags: ["wuhan", "guanggu", "junction", "real-data", "20250721"],
      metrics: { 路口全名: "光谷六小", 数据行: 861, 方向数: 3, 时间点: 287, 总流量: 19898, 平均速度kmh: 31.7, 平均延误s: 26.9, 平均排队m: 18.7, 最大排队m: 66.8, 峰值时刻秒: 30300, 峰值流量: 150 }
    }
  ],
  edges: [
    { id: "rd-gaoxin-guanggu1-to-guanggu6xiao", source: "gg-gaoxin-guanggu1", target: "gg-guanggu6xiao", label: "高新大道西向接入", directed: true, relationType: "road", status: "warning", metrics: { speed: 29.9, bandwidth: 0.72, events: 463, 平均延误s: 38.7 } },
    { id: "rd-guanggu6xiao-to-jiayuan-chuangye", source: "gg-guanggu6xiao", target: "gg-jiayuan-chuangye", label: "光谷片区南向联系", directed: true, relationType: "road", status: "online", metrics: { speed: 33.4, bandwidth: 0.63, events: 236, 平均延误s: 24.6 } },
    { id: "rd-xinzhu-minzu-to-xiongchu-minzu", source: "gg-xinzhu-minzu", target: "gg-xiongchu-minzu", label: "民族大道南向", directed: true, relationType: "road", status: "warning", metrics: { speed: 26.7, bandwidth: 0.79, events: 517, 平均延误s: 44.1 } },
    { id: "rd-xinzhu-minzu-to-guanggu6xiao", source: "gg-xinzhu-minzu", target: "gg-guanggu6xiao", label: "新竹路-光谷片区", directed: true, relationType: "road", status: "warning", metrics: { speed: 27.6, bandwidth: 0.58, events: 319, 平均延误s: 39.9 } },
    { id: "rd-jiayuan-chuangye-to-xiongchu-jiayuan", source: "gg-jiayuan-chuangye", target: "gg-xiongchu-jiayuan", label: "佳园路南向", directed: true, relationType: "road", status: "online", metrics: { speed: 34.0, bandwidth: 0.66, events: 381, 平均延误s: 25.2 } },
    { id: "rd-xiongchu-minzu-to-xiongchu-jiayuan", source: "gg-xiongchu-minzu", target: "gg-xiongchu-jiayuan", label: "雄楚大道西段", directed: true, relationType: "road", status: "warning", metrics: { speed: 31.3, bandwidth: 0.85, events: 517, 平均延误s: 31.6 } },
    { id: "rd-xiongchu-jiayuan-to-xiongchu-xiongzhuang", source: "gg-xiongchu-jiayuan", target: "gg-xiongchu-xiongzhuang", label: "雄楚大道东段", directed: true, relationType: "road", status: "warning", metrics: { speed: 29.8, bandwidth: 0.77, events: 944, 平均延误s: 34.6 } },
    { id: "rd-gaoxin-guanggu1-to-xiongchu-xiongzhuang", source: "gg-gaoxin-guanggu1", target: "gg-xiongchu-xiongzhuang", label: "光谷一路南北联系", directed: true, relationType: "road", status: "warning", metrics: { speed: 28.4, bandwidth: 0.69, events: 463, 平均延误s: 39.9 } },
    { id: "ctx-xiongchu-corridor-backflow", source: "gg-xiongchu-xiongzhuang", target: "gg-xiongchu-minzu", label: "雄楚走廊态势回流", directed: true, relationType: "context", status: "syncing", metrics: { speed: 30.0, bandwidth: 0.92, events: 7, 共享指标: "流量/排队/延误" } }
  ],
  resources: [
    { id: "src-flow-xinzhu-minzu", label: "新竹-民族流量表", resourceType: "detector", anchorAgentId: "gg-xinzhu-minzu", height: 86, direction: "input", status: "online", metrics: { 文件: "1_processed.xlsx", 原始文件: "新竹路与民族大道路口流量数据-20250721.xls", 记录数: 864 } },
    { id: "src-flow-xiongchu-minzu", label: "雄楚-民族流量表", resourceType: "detector", anchorAgentId: "gg-xiongchu-minzu", height: 92, direction: "input", status: "online", metrics: { 文件: "2_processed.xlsx", 原始文件: "雄楚大道民族大道路口流量数据-20250721.xls", 记录数: 1148 } },
    { id: "src-flow-xiongchu-xiongzhuang", label: "雄楚-雄庄流量表", resourceType: "detector", anchorAgentId: "gg-xiongchu-xiongzhuang", height: 88, direction: "input", status: "online", metrics: { 文件: "3_processed.xlsx", 原始文件: "雄楚大道雄庄路路口流量数据-20250721.xls", 记录数: 432 } },
    { id: "src-flow-jiayuan-chuangye", label: "佳园-创业街流量表", resourceType: "detector", anchorAgentId: "gg-jiayuan-chuangye", height: 84, direction: "input", status: "online", metrics: { 文件: "4_processed.xlsx", 原始文件: "佳园路光谷创业街路口流量数据-20250721.xls", 记录数: 1148 } },
    { id: "src-flow-xiongchu-jiayuan", label: "雄楚-佳园流量表", resourceType: "detector", anchorAgentId: "gg-xiongchu-jiayuan", height: 90, direction: "input", status: "online", metrics: { 文件: "5_processed.xlsx", 原始文件: "雄楚大道佳园路路口流量数据-20250721.xls", 记录数: 1144 } },
    { id: "src-flow-gaoxin-guanggu1", label: "高新-光谷一路流量表", resourceType: "detector", anchorAgentId: "gg-gaoxin-guanggu1", height: 96, direction: "input", status: "online", metrics: { 文件: "6_processed.xlsx", 原始文件: "高新大道光谷一路路口流量数据-20250721.xls", 记录数: 1144 } },
    { id: "src-flow-guanggu6xiao", label: "光谷六小流量表", resourceType: "detector", anchorAgentId: "gg-guanggu6xiao", height: 82, direction: "input", status: "online", metrics: { 文件: "7_processed.xlsx", 原始文件: "光谷六小路口流量数据-20250721.xls", 记录数: 861 } },
    { id: "src-phase-wuhan", label: "东湖高新区阶段配时", resourceType: "controller", anchorAgentId: "gg-guanggu6xiao", height: -76, direction: "output", status: "online", metrics: { 来源: "武汉东湖高新区路口阶段配时", 年份: 2023, 类型: "信号相位/阶段配时" } },
    { id: "src-timeseries", label: "交通指标时序库", resourceType: "database", anchorAgentId: "gg-xiongchu-jiayuan", height: -118, direction: "bidirectional", status: "online", metrics: { 写入指标: "flow/speed/queue/delay", 今日样本: 6602, lagMs: 16 } },
    { id: "src-map-files", label: "高新区空间底图", resourceType: "storage", anchorAgentId: "gg-xinzhu-minzu", height: -136, direction: "bidirectional", status: "online", metrics: { 文件夹: "2空间数据", 类型: "shp/shx", 用途: "空间约束与展示" } },
    { id: "src-sim-eval", label: "交通控制仿真器", resourceType: "simulator", anchorAgentId: "gg-gaoxin-guanggu1", height: -104, direction: "bidirectional", status: "syncing", metrics: { 场景: "武汉光谷走廊", 步长s: 300, driftMs: 6 } },
    { id: "src-event-store", label: "演示事件缓存", resourceType: "database", anchorAgentId: "gg-xiongchu-minzu", height: -92, direction: "bidirectional", status: "online", metrics: { 事件: 7, 状态: "ready" } }
  ],
  trend: [
    { t: 0, value: 0.28 },
    { t: 1, value: 0.34 },
    { t: 2, value: 0.43 },
    { t: 3, value: 0.58 },
    { t: 4, value: 0.71 },
    { t: 5, value: 0.66 },
    { t: 6, value: 0.54 },
    { t: 7, value: 0.61 },
    { t: 8, value: 0.77 },
    { t: 9, value: 0.82 },
    { t: 10, value: 0.68 },
    { t: 11, value: 0.49 }
  ],
  events: [
    { id: "evt-wuhan-001", severity: "warning", title: "新竹-民族平均延误 53.0s，低速运行", targetId: "gg-xinzhu-minzu", time: "2025-07-21 08:15" },
    { id: "evt-wuhan-002", severity: "warning", title: "佳园-创业街最大排队长度 144.5m", targetId: "gg-jiayuan-chuangye", time: "2025-07-21 18:20" },
    { id: "evt-wuhan-003", severity: "info", title: "7 个路口处理表共 6602 条聚合记录", targetId: "src-timeseries", time: "数据批次" },
    { id: "evt-wuhan-004", severity: "info", title: "雄楚走廊态势已回流到世界模型工作流", targetId: "ctx-xiongchu-corridor-backflow", time: "演示运行中" }
  ]
};