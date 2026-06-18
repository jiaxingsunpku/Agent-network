import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Focus, Minus, Plus, Search } from "lucide-react";
import { WorldModelRuntime } from "../types";

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
}

const MAP_URL = "/large-traffic-map.json";

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

export function LargeTrafficMapView({ search, runtime }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const camera = useRef<CameraState>({ x: 0, y: 0, scale: 0.08 });
  const drag = useRef({ active: false, lastX: 0, lastY: 0, moved: false });
  const hover = useRef<HitTarget>(null);
  const [hoverState, setHoverState] = useState<HitTarget>(null);
  const [selected, setSelected] = useState<HitTarget>(null);
  const [data, setData] = useState<LargeTrafficMapData | null>(null);
  const [error, setError] = useState("");
  const [pulseFrame, setPulseFrame] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch(MAP_URL, { cache: "force-cache" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((payload: LargeTrafficMapData) => {
        if (!cancelled) setData(payload);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    const fitScale = Math.min((rect.width - 100) / width, (rect.height - 90) / height);
    const scale = fitScale * 2.08;
    const focusX = data.bounds.minX + width * 0.66;
    const focusY = data.bounds.minY + height * 0.52;
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
      const congestion = liveCongestion(road.congestion, road.id, runtimeFrame, networkPressure);
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
      const congestion = liveCongestion(inter.congestion, inter.id, runtimeFrame, networkPressure);
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