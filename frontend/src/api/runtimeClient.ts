import { WorldModelRuntime } from "../types";

const HOT_INTERSECTIONS = [
  ["gg-xinzhu-minzu", "新竹-民族", 58131, 23.5, 53.0, 75.7],
  ["gg-xiongchu-minzu", "雄楚-民族", 104637, 29.9, 35.2, 110.9],
  ["gg-xiongchu-xiongzhuang", "雄楚-雄庄", 22413, 26.8, 41.2, 114.3],
  ["gg-jiayuan-chuangye", "佳园-创业街", 32853, 35.1, 22.3, 144.5],
  ["gg-xiongchu-jiayuan", "雄楚-佳园", 55019, 32.8, 28.0, 128.3],
  ["gg-gaoxin-guanggu1", "高新-光谷一路", 67908, 29.9, 38.7, 114.3],
  ["gg-guanggu6xiao", "光谷六小", 19898, 31.7, 26.9, 66.8]
] as const;

function clamp(value: number, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function simClock(frame: number) {
  const seconds = (7 * 3600 + frame * 45) % (22 * 3600);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function trafficState(delay: number): "畅通" | "缓行" | "拥堵" | "严重" {
  if (delay > 52) return "严重";
  if (delay > 38) return "拥堵";
  if (delay > 27) return "缓行";
  return "畅通";
}

export function makeFallbackRuntime(now = Date.now()): WorldModelRuntime {
  const frame = Math.floor(now / 1000);
  const phase = frame / 7;
  const pulse = Math.sin(phase);
  const secondary = Math.cos(phase * 0.63);
  const congestionIndex = clamp(0.54 + pulse * 0.14 + secondary * 0.05, 0.28, 0.86);
  const avgDelaySec = 26 + congestionIndex * 31 + Math.sin(phase * 1.7) * 2.2;
  const avgSpeedKmh = 42 - congestionIndex * 20 + Math.cos(phase * 1.2) * 1.6;
  const maxQueueM = 86 + congestionIndex * 78 + Math.sin(phase * 1.4) * 9;
  const totalFlow = Math.round(353000 + congestionIndex * 19000 + Math.sin(phase * 0.8) * 5200);
  const processedRecords = 6602 + (frame % 360) * 4;
  const chart = Array.from({ length: 12 }, (_, index) => {
    const value = 42 + Math.sin(phase + index * 0.58) * 22 + Math.cos(phase * 0.47 + index) * 8;
    return Math.round(clamp(value, 18, 92));
  });
  const hotIntersections = HOT_INTERSECTIONS.map(([id, label, flow, speed, delay, queue], index) => {
    const wave = Math.sin(phase + index * 0.84);
    const liveDelay = Math.max(12, delay + wave * 7 + congestionIndex * 4);
    const liveSpeed = Math.max(8, speed - wave * 2.8 - congestionIndex * 3);
    return {
      id,
      label,
      flow: Math.round(flow + wave * 720 + congestionIndex * 900),
      speedKmh: Number(liveSpeed.toFixed(1)),
      delaySec: Number(liveDelay.toFixed(1)),
      queueM: Number(Math.max(8, queue + wave * 15 + congestionIndex * 18).toFixed(1)),
      state: trafficState(liveDelay)
    };
  });
  const controlEventSeverity = avgDelaySec > 46 ? "warning" as const : "info" as const;
  const events = [
    {
      id: `runtime-${frame}-control`,
      severity: controlEventSeverity,
      title: `控制策略同步完成，平均延误 ${avgDelaySec.toFixed(1)}s`,
      target: "signal-control",
      time: simClock(frame)
    },
    {
      id: `runtime-${frame}-flow`,
      severity: "info" as const,
      title: `路网指标批次写入 ${processedRecords.toLocaleString("zh-CN")} 条`,
      target: "src-timeseries",
      time: simClock(frame - 1)
    },
    {
      id: `runtime-${frame}-train`,
      severity: "info" as const,
      title: `CoLight 策略训练 episode ${1200 + (frame % 280)}，奖励 ${(88 + Math.sin(phase) * 6).toFixed(1)}`,
      target: "signal-train",
      time: simClock(frame - 2)
    }
  ];
  return {
    generatedAt: new Date(now).toISOString(),
    frame,
    simTime: simClock(frame),
    mode: "sumo_stream",
    status: {
      label: "系统运行中",
      pipeline: "SUMO 交通仿真流",
      latencyMs: Math.round(16 + Math.abs(Math.sin(phase * 1.5)) * 9),
      tickMs: 1000,
      recordsPerMin: Math.round(820 + congestionIndex * 210),
      runningJobs: 5
    },
    traffic: {
      totalFlow,
      avgSpeedKmh: Number(avgSpeedKmh.toFixed(1)),
      avgDelaySec: Number(avgDelaySec.toFixed(1)),
      maxQueueM: Number(maxQueueM.toFixed(1)),
      congestionIndex: Number(congestionIndex.toFixed(3)),
      activeSignals: 207,
      processedRecords,
      incidents: Math.max(1, Math.round(congestionIndex * 6))
    },
    training: {
      jobName: "CoLight-PPO-signal-control",
      progress: Math.round((frame % 180) / 1.8),
      episode: 1200 + (frame % 280),
      reward: Number((88 + Math.sin(phase) * 6 + congestionIndex * 5).toFixed(1)),
      loss: Number(Math.max(0.02, 0.18 - (frame % 120) * 0.001 + Math.abs(Math.cos(phase)) * 0.02).toFixed(3)),
      etaMin: Math.max(1, 24 - Math.floor((frame % 180) / 8)),
      status: "训练任务运行中"
    },
    video: {
      onlineCameras: 24,
      detectionTasks: 12 + (frame % 4),
      eventCount: 186 + (frame % 45),
      latencyMs: Math.round(78 + Math.abs(Math.cos(phase)) * 18)
    },
    chart,
    hotIntersections,
    events
  };
}

export async function fetchRuntimeSnapshot(): Promise<WorldModelRuntime> {
  const response = await fetch("/api/world-model/runtime", {
    cache: "no-store",
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error(`runtime ${response.status}`);
  return response.json();
}
