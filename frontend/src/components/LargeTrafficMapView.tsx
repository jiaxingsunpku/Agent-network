import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Focus, Map as MapIcon, Minus, Plus, Search } from "lucide-react";
import { AgentNode, NetworkSnapshot, WorldModelRuntime } from "../types";
import {
  fetchSvMaps,
  fetchSvNetwork,
  sendAgentNetworkCommand,
  SvMapEntry,
  SvNetworkGeometry
} from "../api/agentNetworkClient";

function nodeCommandTypes(node: AgentNode | undefined): string[] {
  const raw = node?.metrics?.commandTypes ?? node?.metrics?.command_types;
  return Array.isArray(raw) ? (raw as string[]) : [];
}

interface LargeMapBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

interface LargeMapRoad {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  speed: number;
  lanes: number;
  length: number;
  congestion: number;
  major: boolean;
}

interface LargeMapIntersection {
  id: string;
  label: string;
  x: number;
  y: number;
  incoming: number;
  outgoing: number;
  incomingLanes: number;
  outgoingLanes: number;
  priority: number;
  congestion: number;
  hasTrafficLight: boolean;
}

interface LargeTrafficMapData {
  source: string;
  title: string;
  description: string;
  stats: {
    nodes: number;
    intersections: number;
    edges: number;
    lanes: number;
    origins: number;
    destinations: number;
  };
  bounds: LargeMapBounds;
  roads: LargeMapRoad[];
  intersections: LargeMapIntersection[];
}

interface CameraState {
  x: number;
  y: number;
  scale: number;
}

type HitTarget =
  | { kind: "intersection"; id: string; label: string; x: number; y: number; data: LargeMapIntersection }
  | { kind: "road"; id: string; label: string; x: number; y: number; data: LargeMapRoad }
  | null;

interface Props {
  search: string;
  runtime?: WorldModelRuntime;
  snapshot?: NetworkSnapshot;
}

const MAP_URL = "/large-traffic-map.json";

// 真实 SV 路网几何（网关 /sv-network）→ 本组件的渲染数据形态。
function svGeometryToMapData(geo: SvNetworkGeometry): LargeTrafficMapData {
  const roads: LargeMapRoad[] = geo.edges.map((e) => ({
    id: e.id,
    x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2,
    speed: 0,
    lanes: e.lanes,
    length: e.length,
    congestion: 0, // SV 边无逐边拥堵；路口级拥堵走 intersection.congestion（真实）
    major: e.lanes >= 2
  }));
  const intersections: LargeMapIntersection[] = geo.junctions.map((j) => ({
    id: j.id,
    label: `路口 ${j.id}`,
    x: j.x, y: j.y,
    incoming: 0, outgoing: 0, incomingLanes: 0, outgoingLanes: 0,
    priority: 30,
    congestion: j.congestion, // SV 真实 congestion_level
    hasTrafficLight: j.junction_type === "traffic_light"
  }));
  return {
    source: "signalvision",
    title: "SignalVision 真实路网",
    description: `光谷 ${geo.junction_count} 路口路网（SV /api/network）`,
    stats: { nodes: geo.junctions.length, intersections: geo.junctions.length, edges: geo.edges.length, lanes: 0, origins: 0, destinations: 0 },
    bounds: geo.bounds,
    roads,
    intersections
  };
}

function congestionColor(value: number) {
  if (value >= 0.78) return "#e05243";
  if (value >= 0.6) return "#e08b25";
  if (value >= 0.42) return "#d2b82f";
  if (value >= 0.25) return "#7eaf53";
  return "#4c9c64";
}

function congestionLabel(value: number) {
  if (value >= 0.78) return "严重拥堵";
  if (value >= 0.6) return "拥堵";
  if (value >= 0.42) return "缓行";
  if (value >= 0.25) return "基本畅通";
  return "畅通";
}

function numericSeed(id: string) {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) hash = (hash * 31 + id.charCodeAt(index)) % 997;
  return hash;
}

function clamp(value: number, min = 0, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function liveCongestion(base: number, id: string, frame: number, pressure: number) {
  const seed = numericSeed(id) / 997;
  const wave = Math.sin(frame * 0.42 + seed * 8.1) * 0.08 + Math.cos(frame * 0.19 + seed * 5.4) * 0.035;
  return clamp(base + (pressure - 0.54) * 0.22 + wave, 0.08, 0.94);
}

function distanceToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const dx = bx - ax;
  const dy = by - ay;
  if (dx === 0 && dy === 0) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function LargeTrafficMapView({ search, runtime, snapshot }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const camera = useRef<CameraState>({ x: 0, y: 0, scale: 0.08 });
  const drag = useRef({ active: false, lastX: 0, lastY: 0, moved: false });
  const hover = useRef<HitTarget>(null);
  const mounted = useRef(true);
  const [hoverState, setHoverState] = useState<HitTarget>(null);
  const [selected, setSelected] = useState<HitTarget>(null);
  const [data, setData] = useState<LargeTrafficMapData | null>(null);
  const [error, setError] = useState("");
  const [pulseFrame, setPulseFrame] = useState(0);
  // 切图（set_signal_map）：可用地图列表、当前选择、下发状态/反馈。
  const [maps, setMaps] = useState<SvMapEntry[]>([]);
  const [selectedMap, setSelectedMap] = useState("");
  const [switching, setSwitching] = useState(false);
  const [mapNotice, setMapNotice] = useState("");

  // 能接收 set_signal_map 的 SV 执行体（snapshot registry）；缺失则切图不可用。
  const mapExec = useMemo(
    () => snapshot?.nodes.find((node) => nodeCommandTypes(node).includes("set_signal_map")),
    [snapshot]
  );

  // 重取真实 SV 路网几何并重绘（切图后轮询用）。返回新路口数（不可达=null），供校验几何是否真的变了。
  const reloadSvNetwork = useCallback(async () => {
    const geo = await fetchSvNetwork();
    if (!geo) return null;
    if (mounted.current) setData(svGeometryToMapData(geo));
    return geo.junctions.length;
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      // 先尝试真实 SV 路网（网关 /sv-network relay SV /api/network）；不可达再回落静态演示图。
      const geo = await fetchSvNetwork();
      if (cancelled) return;
      if (geo) {
        setData(svGeometryToMapData(geo));
        return;
      }
      try {
        const response = await fetch(MAP_URL, { cache: "force-cache" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as LargeTrafficMapData;
        if (!cancelled) setData(payload);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  // 拉取 SV 可用地图列表（切图下拉）；不可达返回 []（不显示下拉）。
  useEffect(() => {
    let cancelled = false;
    fetchSvMaps().then((list) => {
      if (!cancelled) setMaps(list);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // 切换 SV 路网：选中 → set_signal_map 命令（→网关 /commands→执行体→SV /api/load-map）→ 轮询重取几何重绘。
  const switchMap = useCallback(
    async (mapPath: string) => {
      if (!mapPath || switching) return;
      setSelectedMap(mapPath);
      if (!mapExec) {
        setMapNotice("无在线 set_signal_map 执行体（traffic-exec-sv-001 未注册/离线）");
        return;
      }
      setSwitching(true);
      setMapNotice(`切图命令下发中 · ${mapPath}`);
      const prevJc = data?.stats.intersections ?? null; // 切图前路口数，用于校验几何是否真的变了
      try {
        const resp = await sendAgentNetworkCommand({
          target_agent_id: mapExec.id,
          command_type: "set_signal_map",
          payload: { map_path: mapPath },
          expires_in_sec: 120
        });
        setMapNotice(`已下发切图 ${mapPath} → ${resp.status}，核验路网中…`);
        // 命令经 Kafka→执行体→SV load-map 有延迟；轮询重取 /sv-network，直到路口数变化（真切图）或超时。
        // 注意：只宣称「已切换」当几何确实变了——避免命令异步失败 / SV 未换图时误报成功。
        let tries = 0;
        const poll = async () => {
          if (!mounted.current) return;
          tries += 1;
          const jc = await reloadSvNetwork();
          if (!mounted.current) return;
          if (jc != null && prevJc != null && jc !== prevJc) {
            setMapNotice(`已切换路网 ${mapPath}（${jc} 路口）`);
            return;
          }
          if (tries >= 8) {
            setMapNotice(
              jc == null
                ? "已下发，但 SV 路网暂不可达"
                : `已下发 ${mapPath}，但路网几何未变（可能同图 / SV 未换图，详见执行体 ack）`
            );
            return;
          }
          window.setTimeout(poll, 1500);
        };
        window.setTimeout(poll, 1500);
      } catch (error) {
        setMapNotice(`切图失败：${error instanceof Error ? error.message : String(error)}`);
      } finally {
        if (mounted.current) setSwitching(false);
      }
    },
    [data, mapExec, reloadSvNetwork, switching]
  );

  const query = search.trim().toLowerCase();

  useEffect(() => {
    const id = window.setInterval(() => setPulseFrame((value) => value + 1), 650);
    return () => window.clearInterval(id);
  }, []);

  const mapY = useCallback((rawY: number) => {
    if (!data) return rawY;
    return data.bounds.maxY - rawY + data.bounds.minY;
  }, [data]);

  const worldToScreen = useCallback((x: number, y: number) => ({
    x: camera.current.x + x * camera.current.scale,
    y: camera.current.y + mapY(y) * camera.current.scale
  }), [mapY]);

  const screenToWorld = useCallback((x: number, y: number) => {
    if (!data) return { x: 0, y: 0 };
    const wx = (x - camera.current.x) / camera.current.scale;
    const mappedY = (y - camera.current.y) / camera.current.scale;
    return { x: wx, y: data.bounds.maxY - mappedY + data.bounds.minY };
  }, [data]);

  const resetView = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;
    const rect = canvas.getBoundingClientRect();
    const width = data.bounds.maxX - data.bounds.minX;
    const height = data.bounds.maxY - data.bounds.minY;
    const realFit = data.source === "signalvision"; // 真实 SV 路网较小较密 → 居中铺满，不像大演示图那样放大裁切
    const fitScale = Math.min((rect.width - 100) / width, (rect.height - 90) / height);
    const scale = fitScale * (realFit ? 0.9 : 2.08);
    const focusX = data.bounds.minX + width * (realFit ? 0.5 : 0.66);
    const focusY = data.bounds.minY + height * (realFit ? 0.5 : 0.52);
    const mappedFocusY = data.bounds.maxY - focusY + data.bounds.minY;
    camera.current = {
      scale,
      x: rect.width / 2 - focusX * scale,
      y: rect.height / 2 - mappedFocusY * scale
    };
  }, [data]);

  const highlightIds = useMemo(() => {
    if (!data || !query) return new Set<string>();
    const ids = new Set<string>();
    data.intersections.forEach((inter) => {
      if (`${inter.id} ${inter.label}`.toLowerCase().includes(query)) ids.add(inter.id);
    });
    return ids;
  }, [data, query]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const width = rect.width;
    const height = rect.height;
    const isReal = data.source === "signalvision"; // 真实 SV 路网：用真实拥堵，不叠 mock 推演波
    const runtimeFrame = (runtime?.frame ?? 0) + pulseFrame * 0.5;
    const networkPressure = runtime?.traffic.congestionIndex ?? 0.54;
    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, "#f6faf8");
    bg.addColorStop(1, "#e5eee9");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    const margin = 90;
    const minWorldX = (0 - camera.current.x - margin) / camera.current.scale;
    const maxWorldX = (width - camera.current.x + margin) / camera.current.scale;
    const minMappedY = (0 - camera.current.y - margin) / camera.current.scale;
    const maxMappedY = (height - camera.current.y + margin) / camera.current.scale;

    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    for (const road of data.roads) {
      const y1 = mapY(road.y1);
      const y2 = mapY(road.y2);
      const minX = Math.min(road.x1, road.x2);
      const maxX = Math.max(road.x1, road.x2);
      const minY = Math.min(y1, y2);
      const maxY = Math.max(y1, y2);
      if (maxX < minWorldX || minX > maxWorldX || maxY < minMappedY || minY > maxMappedY) continue;
      if (camera.current.scale < 0.055 && !road.major) continue;
      const a = worldToScreen(road.x1, road.y1);
      const b = worldToScreen(road.x2, road.y2);
      const congestion = isReal ? road.congestion : liveCongestion(road.congestion, road.id, runtimeFrame, networkPressure);
      const color = congestionColor(congestion);
      ctx.globalAlpha = road.major ? 0.82 : 0.34;
      ctx.strokeStyle = "rgba(255,255,255,.72)";
      ctx.lineWidth = road.major ? 4.1 : 2.2;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
      ctx.globalAlpha = road.major ? 0.9 : 0.58;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(0.75, (road.major ? 1.5 : 0.85) + Math.min(road.lanes, 4) * 0.12);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    const selectedId = selected?.kind === "intersection" ? selected.id : "";
    const hoverId = hover.current?.kind === "intersection" ? hover.current.id : "";
    const labelLimit = camera.current.scale > 0.12 ? 55 : camera.current.scale > 0.075 ? 24 : 10;
    let labels = 0;
    data.intersections.forEach((inter) => {
      const p = worldToScreen(inter.x, inter.y);
      if (p.x < -20 || p.x > width + 20 || p.y < -20 || p.y > height + 20) return;
      const isSelected = selectedId === inter.id;
      const isHover = hoverId === inter.id;
      const isMatch = highlightIds.has(inter.id);
      const important = labels < labelLimit && inter.priority > 22;
      const congestion = isReal ? inter.congestion : liveCongestion(inter.congestion, inter.id, runtimeFrame, networkPressure);
      const color = congestionColor(congestion);
      const isLivePulse = runtime ? (Math.floor(runtimeFrame + numericSeed(inter.id)) % 13 === 0) : false;
      const radius = Math.max(4, Math.min(9, 3.5 + inter.priority / 9)) + (isLivePulse ? 1.2 : 0);

      ctx.globalAlpha = isSelected || isHover || isMatch ? 0.2 : 0.1;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius + 12, 0, Math.PI * 2);
      ctx.fill();

      ctx.globalAlpha = 0.96;
      ctx.fillStyle = color;
      ctx.strokeStyle = "rgba(255,255,255,.86)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      if (isLivePulse) {
        ctx.globalAlpha = 0.2;
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius + 17 + Math.sin(runtimeFrame) * 3, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (isSelected || isHover || isMatch) {
        ctx.strokeStyle = "#2563eb";
        ctx.lineWidth = 2.6;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius + 9, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (isSelected || isHover || isMatch || important) {
        labels += 1;
        ctx.font = `${isSelected || isHover ? 700 : 650} 11px Inter, Microsoft YaHei, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillStyle = "#172033";
        ctx.strokeStyle = "rgba(255,255,255,.94)";
        ctx.lineWidth = 5;
        const text = inter.label.replace("信号路口 ", "路口 ");
        ctx.strokeText(text, p.x, p.y - radius - 8);
        ctx.fillText(text, p.x, p.y - radius - 8);
      }
    });

    ctx.globalAlpha = 1;
    const selectedRoad = selected?.kind === "road" ? selected.data : null;
    if (selectedRoad) {
      const a = worldToScreen(selectedRoad.x1, selectedRoad.y1);
      const b = worldToScreen(selectedRoad.x2, selectedRoad.y2);
      ctx.strokeStyle = "#2563eb";
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

  }, [data, highlightIds, mapY, pulseFrame, runtime, selected, worldToScreen]);

  useEffect(() => {
    if (!data) return;
    resetView();
  }, [data, resetView]);

  useEffect(() => {
    draw();
  }, [draw]);

  const hitTest = useCallback((x: number, y: number): HitTarget => {
    if (!data) return null;
    const world = screenToWorld(x, y);
    const scale = camera.current.scale;
    for (const inter of data.intersections) {
      const distance = Math.hypot(world.x - inter.x, world.y - inter.y);
      const radius = Math.max(9 / scale, 16);
      if (distance <= radius) return { kind: "intersection", id: inter.id, label: inter.label, x, y, data: inter };
    }
    if (scale > 0.055) {
      for (const road of data.roads) {
        if (!road.major && scale < 0.11) continue;
        const distance = distanceToSegment(world.x, world.y, road.x1, road.y1, road.x2, road.y2);
        if (distance <= Math.max(6 / scale, 18)) {
          return { kind: "road", id: road.id, label: `道路 ${road.id}`, x, y, data: road };
        }
      }
    }
    return null;
  }, [data, screenToWorld]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const point = (event: MouseEvent | WheelEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const onMouseDown = (event: MouseEvent) => {
      const p = point(event);
      drag.current = { active: true, lastX: p.x, lastY: p.y, moved: false };
    };
    const onMouseMove = (event: MouseEvent) => {
      const p = point(event);
      if (drag.current.active) {
        const dx = p.x - drag.current.lastX;
        const dy = p.y - drag.current.lastY;
        camera.current.x += dx;
        camera.current.y += dy;
        drag.current.lastX = p.x;
        drag.current.lastY = p.y;
        drag.current.moved = drag.current.moved || Math.abs(dx) + Math.abs(dy) > 2;
        draw();
        return;
      }
      const hit = hitTest(p.x, p.y);
      hover.current = hit;
      setHoverState(hit);
      draw();
    };
    const onMouseUp = (event: MouseEvent) => {
      if (!drag.current.active) return;
      const p = point(event);
      const wasDrag = drag.current.moved;
      drag.current.active = false;
      if (!wasDrag) {
        const hit = hitTest(p.x, p.y);
        setSelected(hit);
      }
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (!data) return;
      const p = point(event);
      const before = screenToWorld(p.x, p.y);
      const next = Math.max(0.018, Math.min(2.5, camera.current.scale * (event.deltaY > 0 ? 0.88 : 1.14)));
      camera.current.scale = next;
      const mappedY = data.bounds.maxY - before.y + data.bounds.minY;
      camera.current.x = p.x - before.x * next;
      camera.current.y = p.y - mappedY * next;
      draw();
    };
    const onResize = () => {
      resetView();
      draw();
    };
    canvas.addEventListener("mousedown", onMouseDown);
    canvas.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("resize", onResize);
    return () => {
      canvas.removeEventListener("mousedown", onMouseDown);
      canvas.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", onResize);
    };
  }, [data, draw, hitTest, resetView, screenToWorld]);

  const zoom = (factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;
    const rect = canvas.getBoundingClientRect();
    const center = { x: rect.width / 2, y: rect.height / 2 };
    const before = screenToWorld(center.x, center.y);
    const next = Math.max(0.018, Math.min(2.5, camera.current.scale * factor));
    camera.current.scale = next;
    const mappedY = data.bounds.maxY - before.y + data.bounds.minY;
    camera.current.x = center.x - before.x * next;
    camera.current.y = center.y - mappedY * next;
    draw();
  };

  const clearSelection = () => setSelected(null);

  return (
    <section className="large-traffic-stage">
      <canvas ref={canvasRef} className="large-traffic-canvas" />

      <div className="large-map-controls" aria-label="交通地图控制">
        <button onClick={() => zoom(1.18)} title="放大"><Plus size={18} /></button>
        <button onClick={() => zoom(0.82)} title="缩小"><Minus size={18} /></button>
        <button onClick={resetView} title="回中"><Focus size={18} /></button>
      </div>

      {data?.source === "signalvision" && maps.length > 0 && (
        <div className="large-map-mapswitch" aria-label="切换路网">
          <label>
            <MapIcon size={15} />
            <select
              value={selectedMap}
              disabled={switching}
              onChange={(event) => switchMap(event.target.value)}
              title={mapExec ? "下发 set_signal_map 切换 SV 路网" : "无在线 set_signal_map 执行体"}
            >
              <option value="">切换路网…（{maps.length}）</option>
              {maps.map((m) => (
                <option key={m.path} value={m.path}>{m.name}</option>
              ))}
            </select>
          </label>
          {mapNotice && <small className={switching ? "busy" : ""}>{mapNotice}</small>}
        </div>
      )}

      {error && <div className="large-map-status-chip error">地图加载失败：{error}</div>}

      <div className="large-map-legend">
        <span><i style={{ background: "#4c9c64" }} />畅通</span>
        <span><i style={{ background: "#d2b82f" }} />缓行</span>
        <span><i style={{ background: "#e08b25" }} />拥堵</span>
        <span><i style={{ background: "#e05243" }} />严重</span>
      </div>

      {query && (
        <div className="large-map-search-chip">
          <Search size={15} /> 搜索高亮：{search} · {highlightIds.size} 个路口
        </div>
      )}

      {hoverState && (
        <div className="large-map-tooltip" style={{ left: hoverState.x + 16, top: hoverState.y + 14 }}>
          <b>{hoverState.label}</b>
          {hoverState.kind === "intersection" ? (
            <span>{congestionLabel(hoverState.data.congestion)} · {hoverState.data.incomingLanes + hoverState.data.outgoingLanes} 关联车道</span>
          ) : (
            <span>{congestionLabel(hoverState.data.congestion)} · {hoverState.data.lanes} 车道 · {hoverState.data.speed.toFixed(1)} m/s</span>
          )}
        </div>
      )}

      {selected && (
        <div className="large-map-detail-card">
          <button onClick={clearSelection} title="关闭">×</button>
          <div className="traffic-kicker">{selected.kind === "intersection" ? "信号路口详情" : "道路边详情"}</div>
          <h3>{selected.label}</h3>
          {selected.kind === "intersection" ? (
            <div className="traffic-detail-grid">
              <span><b>{selected.data.incoming}</b>进入道路</span>
              <span><b>{selected.data.outgoing}</b>离开道路</span>
              <span><b>{selected.data.incomingLanes}</b>进入车道</span>
              <span><b>{selected.data.outgoingLanes}</b>离开车道</span>
              <span><b>{congestionLabel(selected.data.congestion)}</b>估计状态</span>
              <span><b>{selected.data.priority}</b>复杂度</span>
            </div>
          ) : (
            <div className="traffic-detail-grid">
              <span><b>{selected.data.lanes}</b>车道</span>
              <span><b>{selected.data.length.toFixed(0)}m</b>长度</span>
              <span><b>{selected.data.speed.toFixed(1)}m/s</b>限速</span>
              <span><b>{congestionLabel(selected.data.congestion)}</b>估计状态</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}