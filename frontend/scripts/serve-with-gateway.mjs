import { createReadStream, existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "..");
const distDir = path.join(root, "dist");
const port = Number(process.env.PORT || 18184);
const host = process.env.HOST || "0.0.0.0";
const gateway = (process.env.AGENT_NETWORK_GATEWAY || "http://127.0.0.1:18080").replace(/\/$/, "");

const contentTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon"
};

const serverStartedAt = Date.now();
const runtimeIntersections = [
  ["gg-xinzhu-minzu", "新竹-民族", 58131, 23.5, 53.0, 75.7],
  ["gg-xiongchu-minzu", "雄楚-民族", 104637, 29.9, 35.2, 110.9],
  ["gg-xiongchu-xiongzhuang", "雄楚-雄庄", 22413, 26.8, 41.2, 114.3],
  ["gg-jiayuan-chuangye", "佳园-创业街", 32853, 35.1, 22.3, 144.5],
  ["gg-xiongchu-jiayuan", "雄楚-佳园", 55019, 32.8, 28.0, 128.3],
  ["gg-gaoxin-guanggu1", "高新-光谷一路", 67908, 29.9, 38.7, 114.3],
  ["gg-guanggu6xiao", "光谷六小", 19898, 31.7, 26.9, 66.8]
];

function clamp(value, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function simClock(frame) {
  const seconds = (7 * 3600 + frame * 45) % (22 * 3600);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function trafficState(delay) {
  if (delay > 52) return "严重";
  if (delay > 38) return "拥堵";
  if (delay > 27) return "缓行";
  return "畅通";
}

function runtimeSnapshot() {
  const now = Date.now();
  const frame = Math.floor((now - serverStartedAt) / 1000);
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
  const hotIntersections = runtimeIntersections.map(([id, label, flow, speed, delay, queue], index) => {
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
  const controlEventSeverity = avgDelaySec > 46 ? "warning" : "info";
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
    events: [
      {
        id: `runtime-${frame}-control`,
        severity: controlEventSeverity,
        title: `控制策略同步完成，平均延误 ${avgDelaySec.toFixed(1)}s`,
        target: "signal-control",
        time: simClock(frame)
      },
      {
        id: `runtime-${frame}-flow`,
        severity: "info",
        title: `路网指标批次写入 ${processedRecords.toLocaleString("zh-CN")} 条`,
        target: "src-timeseries",
        time: simClock(frame - 1)
      },
      {
        id: `runtime-${frame}-train`,
        severity: "info",
        title: `CoLight 策略训练 episode ${1200 + (frame % 280)}`,
        target: "signal-train",
        time: simClock(frame - 2)
      }
    ]
  };
}

function send(res, status, body, contentType = "text/plain; charset=utf-8") {
  const buffer = Buffer.isBuffer(body) ? body : Buffer.from(String(body));
  res.writeHead(status, {
    "Content-Type": contentType,
    "Content-Length": buffer.length
  });
  res.end(buffer);
}

async function proxyApi(req, res) {
  const url = new URL(req.url ?? "/", gateway);
  const headers = { ...req.headers };
  delete headers.host;

  const upstream = http.request(url, { method: req.method, headers }, (upstreamRes) => {
    res.writeHead(upstreamRes.statusCode || 502, { ...upstreamRes.headers });
    upstreamRes.pipe(res);
  });

  upstream.on("error", (error) => {
    send(res, 502, JSON.stringify({ ok: false, error: error.message }), "application/json; charset=utf-8");
  });

  req.pipe(upstream);
}

function safeResolve(requestPath) {
  const decoded = decodeURIComponent(requestPath.split("?")[0] || "/");
  const normalized = path.normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  const resolved = path.join(distDir, normalized);
  return resolved.startsWith(distDir) ? resolved : distDir;
}

async function serveStatic(req, res) {
  let filePath = safeResolve(req.url ?? "/");
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) {
    filePath = path.join(distDir, "index.html");
  }
  if (!existsSync(filePath)) {
    send(res, 404, "dist/index.html not found. Run npm run build first.");
    return;
  }
  res.writeHead(200, {
    "Content-Type": contentTypes[path.extname(filePath)] || "application/octet-stream"
  });
  createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  try {
    if ((req.url || "").startsWith("/api/world-model/runtime")) {
      send(res, 200, JSON.stringify(runtimeSnapshot()), "application/json; charset=utf-8");
      return;
    }
    if ((req.url || "").startsWith("/api/")) {
      await proxyApi(req, res);
      return;
    }
    await serveStatic(req, res);
  } catch (error) {
    send(res, 500, error instanceof Error ? error.message : String(error));
  }
});

server.listen(port, host, async () => {
  const pkg = JSON.parse(await readFile(path.join(root, "package.json"), "utf-8"));
  console.log(`${pkg.name} serving dist on http://${host}:${port}`);
  console.log(`proxying /api/* to ${gateway}`);
});
