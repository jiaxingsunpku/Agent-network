import { useCallback, useEffect, useRef, useState } from "react";
import { Focus, Minus, Plus } from "lucide-react";
import { AgentEdge, AgentNode, SelectionRef, NetworkSnapshot } from "../types";

interface Props {
  snapshot: NetworkSnapshot;
  selected: SelectionRef | null;
  search: string;
  onSelect: (selection: SelectionRef | null) => void;
}

interface CameraState {
  x: number;
  y: number;
  scale: number;
}

type HoverRef = { kind: "node" | "edge"; id: string; label: string; x: number; y: number } | null;

const statusColor = {
  online: "#8bb65f",
  warning: "#d97706",
  offline: "#33413b",
  syncing: "#5aa469"
};

function hashText(text: string) {
  let value = 0;
  for (let i = 0; i < text.length; i += 1) {
    value = (value * 31 + text.charCodeAt(i)) >>> 0;
  }
  return value;
}

function roadColor(edge: AgentEdge) {
  if (edge.status === "offline") return "#9ca3af";
  const speed = Number(edge.metrics.speed ?? 35);
  if (speed <= 20) return "#dc2626";
  if (edge.status === "warning" || speed < 32) return "#f59e0b";
  if (speed < 42) return "#d6c33a";
  return "#64a65a";
}

function roadPoints(edge: AgentEdge, source: AgentNode, target: AgentNode) {
  return [
    { x: source.position.x, y: source.position.y },
    { x: target.position.x, y: target.position.y }
  ];
}

function strokeSmoothPath(ctx: CanvasRenderingContext2D, points: Array<{ x: number; y: number }>) {
  if (points.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i += 1) {
    ctx.lineTo(points[i].x, points[i].y);
  }
  ctx.stroke();
}

function drawArrow(ctx: CanvasRenderingContext2D, points: Array<{ x: number; y: number }>, color: string, scale: number) {
  const a = points[points.length - 2];
  const b = points[points.length - 1];
  const angle = Math.atan2(b.y - a.y, b.x - a.x);
  const x = b.x - Math.cos(angle) * 18;
  const y = b.y - Math.sin(angle) * 18;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - Math.cos(angle - Math.PI / 7) * (12 / scale), y - Math.sin(angle - Math.PI / 7) * (12 / scale));
  ctx.lineTo(x - Math.cos(angle + Math.PI / 7) * (12 / scale), y - Math.sin(angle + Math.PI / 7) * (12 / scale));
  ctx.closePath();
  ctx.fill();
}

function distanceToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const dx = bx - ax;
  const dy = by - ay;
  if (dx === 0 && dy === 0) return Math.hypot(px - ax, py - ay);
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function TopologyCanvas({ snapshot, selected, search, onSelect }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const camera = useRef<CameraState>({ x: 72, y: 58, scale: 1 });
  const drag = useRef({ active: false, lastX: 0, lastY: 0, moved: false });
  const hover = useRef<HoverRef>(null);
  const [hoverState, setHoverState] = useState<HoverRef>(null);
  const [scaleLabel, setScaleLabel] = useState("100%");

  const nodeMap = new Map(snapshot.nodes.map((node) => [node.id, node]));
  const query = search.trim().toLowerCase();

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#edf3f0";
    ctx.fillRect(0, 0, width, height);

    const bg = ctx.createLinearGradient(0, 0, width, height);
    bg.addColorStop(0, "rgba(255,255,255,0.62)");
    bg.addColorStop(1, "rgba(218,229,224,0.56)");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(camera.current.x, camera.current.y);
    ctx.scale(camera.current.scale, camera.current.scale);

    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    snapshot.edges.forEach((edge) => {
      const source = nodeMap.get(edge.source);
      const target = nodeMap.get(edge.target);
      if (!source || !target) return;
      const points = roadPoints(edge, source, target);
      const color = roadColor(edge);
      const baseWidth = edge.relationType === "context" ? 2.2 : 3.4;
      ctx.globalAlpha = edge.status === "offline" ? 0.28 : 0.76;
      ctx.strokeStyle = "rgba(255, 255, 255, .9)";
      ctx.lineWidth = (baseWidth + 4.8) / camera.current.scale;
      strokeSmoothPath(ctx, points);
      ctx.globalAlpha = edge.status === "offline" ? 0.52 : 0.92;
      ctx.strokeStyle = color;
      ctx.lineWidth = baseWidth / camera.current.scale;
      strokeSmoothPath(ctx, points);

      const laneCount = Math.max(2, Math.min(4, Math.round(Number(edge.metrics.bandwidth ?? 0.6) * 4)));
      const sourcePoint = points[0];
      const targetPoint = points[points.length - 1];
      const angle = Math.atan2(targetPoint.y - sourcePoint.y, targetPoint.x - sourcePoint.x);
      const nx = -Math.sin(angle);
      const ny = Math.cos(angle);
      for (let lane = 1; lane < laneCount; lane += 1) {
        const offset = (lane - (laneCount - 1) / 2) * 5.2;
        const shifted = points.map((point) => ({ x: point.x + nx * offset, y: point.y + ny * offset }));
        ctx.globalAlpha = 0.58;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.15 / camera.current.scale;
        strokeSmoothPath(ctx, shifted);
      }
      ctx.globalAlpha = 1;
    });

    snapshot.edges.forEach((edge) => {
      const source = nodeMap.get(edge.source);
      const target = nodeMap.get(edge.target);
      if (!source || !target) return;
      const isSelected = selected?.kind === "edge" && selected.id === edge.id;
      const isHover = hover.current?.kind === "edge" && hover.current.id === edge.id;
      const match = query && `${edge.id} ${edge.label}`.toLowerCase().includes(query);
      if (!isSelected && !isHover && !match) return;
      const points = roadPoints(edge, source, target);
      const color = isSelected ? "#f6d35f" : "#f97316";
      ctx.globalAlpha = 0.98;
      ctx.shadowColor = color;
      ctx.shadowBlur = 14 / camera.current.scale;
      ctx.strokeStyle = color;
      ctx.lineWidth = 6.5 / camera.current.scale;
      strokeSmoothPath(ctx, points);
      ctx.shadowBlur = 0;

      if (edge.directed) {
        drawArrow(ctx, points, color, camera.current.scale);
      }
    });

    snapshot.nodes.forEach((node) => {
      const isSelected = selected?.kind === "node" && selected.id === node.id;
      const isHover = hover.current?.kind === "node" && hover.current.id === node.id;
      const match = query && `${node.id} ${node.label} ${node.tags.join(" ")}`.toLowerCase().includes(query);
      const radius = node.nodeType === "region" ? 4.8 : 3.6;
      const pulse = isSelected || isHover || match;

      const glowColor = node.status === "warning" ? "#f97316" : node.status === "offline" ? "#64748b" : "#91c95d";
      ctx.globalAlpha = node.status === "offline" ? 0.2 : 0.42;
      ctx.fillStyle = glowColor;
      ctx.beginPath();
      ctx.arc(node.position.x, node.position.y, radius + 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;

      if (pulse) {
        ctx.shadowColor = "#f97316";
        ctx.shadowBlur = 18 / camera.current.scale;
        ctx.strokeStyle = isSelected ? "#f6d35f" : "#f97316";
        ctx.lineWidth = 3.5 / camera.current.scale;
        ctx.beginPath();
        ctx.arc(node.position.x, node.position.y, radius + 15, 0, Math.PI * 2);
        ctx.stroke();
        ctx.shadowBlur = 0;
      }

      ctx.fillStyle = pulse ? "#f6d35f" : statusColor[node.status];
      ctx.beginPath();
      ctx.arc(node.position.x, node.position.y, radius, 0, Math.PI * 2);
      ctx.fill();

      const showLabel = pulse || snapshot.nodes.length <= 9;
      if (!showLabel) return;
      ctx.save();
      ctx.scale(1 / camera.current.scale, 1 / camera.current.scale);
      const sx = node.position.x * camera.current.scale;
      const sy = node.position.y * camera.current.scale;
      ctx.font = `${pulse ? 700 : 650} ${pulse ? 12 : 11}px Inter, Microsoft YaHei, sans-serif`;
      ctx.textAlign = "center";
      ctx.fillStyle = pulse ? "#dfe8bf" : "#1f3328";
      ctx.strokeStyle = pulse ? "rgba(6,8,7,.95)" : "rgba(255,255,255,.92)";
      ctx.lineWidth = pulse ? 4 : 5;
      ctx.strokeText(node.label, sx, sy + (radius + 18) * camera.current.scale);
      ctx.fillText(node.label, sx, sy + (radius + 18) * camera.current.scale);
      ctx.restore();
    });

    ctx.restore();
  }, [nodeMap, query, selected, snapshot.edges, snapshot.nodes]);

  const resetView = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const xs = snapshot.nodes.map((node) => node.position.x);
    const ys = snapshot.nodes.map((node) => node.position.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const scale = Math.min((rect.width - 140) / (maxX - minX || 1), (rect.height - 120) / (maxY - minY || 1), 1.25);
    camera.current = {
      scale,
      x: rect.width / 2 - ((minX + maxX) / 2) * scale,
      y: rect.height / 2 - ((minY + maxY) / 2) * scale
    };
    setScaleLabel(`${Math.round(scale * 100)}%`);
    draw();
  }, [draw, snapshot.nodes]);

  const screenToWorld = useCallback((x: number, y: number) => ({
    x: (x - camera.current.x) / camera.current.scale,
    y: (y - camera.current.y) / camera.current.scale
  }), []);

  const hitTest = useCallback((x: number, y: number) => {
    const world = screenToWorld(x, y);
    for (const node of snapshot.nodes) {
      if (Math.hypot(world.x - node.position.x, world.y - node.position.y) <= 22 / camera.current.scale + 13) {
        return { kind: "node" as const, id: node.id, label: node.label };
      }
    }
    for (const edge of snapshot.edges) {
      const source = nodeMap.get(edge.source);
      const target = nodeMap.get(edge.target);
      if (!source || !target) continue;
      const distance = distanceToSegment(world.x, world.y, source.position.x, source.position.y, target.position.x, target.position.y);
      if (distance <= 8 / camera.current.scale) return { kind: "edge" as const, id: edge.id, label: edge.label };
    }
    return null;
  }, [nodeMap, screenToWorld, snapshot.edges, snapshot.nodes]);

  useEffect(() => {
    resetView();
  }, []);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const resize = () => draw();
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
      hover.current = hit ? { ...hit, x: p.x, y: p.y } : null;
      setHoverState(hover.current);
      draw();
    };
    const onMouseUp = (event: MouseEvent) => {
      if (!drag.current.active) return;
      const p = point(event);
      const wasDrag = drag.current.moved;
      drag.current.active = false;
      if (!wasDrag) {
        const hit = hitTest(p.x, p.y);
        onSelect(hit ? { kind: hit.kind, id: hit.id } : null);
      }
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const p = point(event);
      const factor = event.deltaY > 0 ? 0.9 : 1.12;
      const next = Math.max(0.35, Math.min(4, camera.current.scale * factor));
      const world = screenToWorld(p.x, p.y);
      camera.current.scale = next;
      camera.current.x = p.x - world.x * next;
      camera.current.y = p.y - world.y * next;
      setScaleLabel(`${Math.round(next * 100)}%`);
      draw();
    };

    canvas.addEventListener("mousedown", onMouseDown);
    canvas.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("resize", resize);
    return () => {
      canvas.removeEventListener("mousedown", onMouseDown);
      canvas.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", resize);
    };
  }, [draw, hitTest, onSelect, screenToWorld]);

  const zoom = (factor: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const center = { x: rect.width / 2, y: rect.height / 2 };
    const world = screenToWorld(center.x, center.y);
    const next = Math.max(0.35, Math.min(4, camera.current.scale * factor));
    camera.current.scale = next;
    camera.current.x = center.x - world.x * next;
    camera.current.y = center.y - world.y * next;
    setScaleLabel(`${Math.round(next * 100)}%`);
    draw();
  };

  return (
    <section className="canvas-stage">
      <canvas ref={canvasRef} className="network-canvas" />
      <div className="map-controls" aria-label="地图控制">
        <button onClick={() => zoom(1.18)} title="放大"><Plus size={18} /></button>
        <button onClick={() => zoom(0.82)} title="缩小"><Minus size={18} /></button>
        <button onClick={resetView} title="回中"><Focus size={18} /></button>
      </div>
      <div className="scale-pill">{scaleLabel}</div>
      {hoverState && (
        <div className="map-tooltip" style={{ left: hoverState.x + 16, top: hoverState.y + 16 }}>
          <b>{hoverState.label}</b>
          <span>{hoverState.kind === "node" ? "智能体节点" : "平面关系"}</span>
        </div>
      )}
    </section>
  );
}
