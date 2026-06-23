import { useEffect, useMemo, useState } from "react";
import { fetchNetworkSnapshot } from "../api/agentNetworkClient";
import { fetchRuntimeSnapshot, makeFallbackRuntime } from "../api/runtimeClient";
import { baseNetworkSnapshot } from "../data/mockNetwork";
import { NetworkSnapshot } from "../types";

function clamp(value: number, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function useGatewaySourceRequested() {
  return new URLSearchParams(window.location.search).get("source") === "gateway";
}

export function useSimulatedSnapshot() {
  const [tick, setTick] = useState(0);
  const [gatewaySnapshot, setGatewaySnapshot] = useState<NetworkSnapshot | null>(null);
  const [runtime, setRuntime] = useState(() => makeFallbackRuntime());
  const gatewaySourceRequested = useGatewaySourceRequested();

  useEffect(() => {
    const id = window.setInterval(() => setTick((value) => value + 1), 1600);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    // 网关未实现 /world-model/runtime（见 README 边界）：首个请求 404 后不再重复打网络，
    // 改为每秒本地推演 makeFallbackRuntime，保持时钟/指标动效又消除每秒 404 噪声。
    let endpointAvailable = true;
    const load = async () => {
      if (!endpointAvailable) {
        if (!cancelled) setRuntime(makeFallbackRuntime());
        return;
      }
      try {
        const next = await fetchRuntimeSnapshot();
        if (!cancelled) setRuntime(next);
      } catch {
        endpointAvailable = false;
        if (!cancelled) setRuntime(makeFallbackRuntime());
      }
    };
    load();
    const id = window.setInterval(load, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!gatewaySourceRequested) {
      setGatewaySnapshot(null);
      return undefined;
    }

    let cancelled = false;
    const load = async () => {
      try {
        const next = await fetchNetworkSnapshot();
        if (!cancelled && next?.nodes.length) setGatewaySnapshot(next);
      } catch {
        if (!cancelled) setGatewaySnapshot(null);
      }
    };
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [gatewaySourceRequested]);

  const simulatedSnapshot = useMemo<NetworkSnapshot>(() => {
    const phase = runtime.frame / 7;
    const nodes = baseNetworkSnapshot.nodes.map((node, index) => {
      const baseLoad = Number(node.metrics.load ?? Math.min(0.9, Math.max(0.18, Number(node.metrics.平均延误s ?? 30) / 80)));
      const wave = Math.sin(phase + index * 0.72) * 0.04;
      const load = clamp(baseLoad + wave);
      const healthDrop = node.status === "offline" ? 48 : node.status === "warning" ? 18 : 4;
      const health = Math.round(clamp(1 - load * 0.14, 0.2, 1) * 100 - healthDrop * 0.12);
      return {
        ...node,
        health,
        metrics: {
          ...node.metrics,
          load: Number(load.toFixed(2)),
          queue: Math.max(0, Math.round(Number(node.metrics.平均排队m ?? 0) + wave * 60))
        }
      };
    });

    const edges = baseNetworkSnapshot.edges.map((edge, index) => {
      const wave = Math.sin(phase * 1.4 + index * 0.51);
      const baseSpeed = Number(edge.metrics.speed ?? 32);
      return {
        ...edge,
        metrics: {
          ...edge.metrics,
          speed: Math.max(0, Number((baseSpeed + wave * 2.4).toFixed(1))),
          bandwidth: Number(clamp(Number(edge.metrics.bandwidth ?? 0.5) + wave * 0.03).toFixed(2))
        }
      };
    });

    const trend = baseNetworkSnapshot.trend.slice(1).concat({
      t: runtime.frame,
      value: Number(clamp(runtime.traffic.congestionIndex).toFixed(2))
    });

    const healthy = nodes.filter((node) => node.status !== "offline").length / nodes.length;

    return {
      ...baseNetworkSnapshot,
      generatedAt: new Date().toISOString(),
      summary: {
        ...baseNetworkSnapshot.summary,
        healthyPercent: Math.round(healthy * 100),
        kafkaLagMs: Math.max(8, Math.round(16 + Math.sin(phase) * 5)),
        updateRate: Math.max(260, Math.round(288 + Math.cos(phase * 0.8) * 9))
      },
      nodes,
      edges,
      trend,
      events: runtime.events.map((event) => ({
        id: event.id,
        severity: event.severity,
        title: event.title,
        targetId: event.target,
        time: event.time
      }))
    };
  }, [runtime]);

  const snapshot = gatewaySourceRequested && gatewaySnapshot ? gatewaySnapshot : simulatedSnapshot;
  const source = gatewaySourceRequested && gatewaySnapshot ? "gateway" : "武汉光谷演示快照";

  return { snapshot, tick, source, runtime };
}