import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { PhysicalResource, SelectionRef, NetworkSnapshot } from "../types";

interface Props {
  snapshot: NetworkSnapshot;
  selected: SelectionRef | null;
  onSelect: (selection: SelectionRef | null) => void;
}

const statusColor = {
  online: 0x2563eb,
  warning: 0xd97706,
  offline: 0x64748b,
  syncing: 0x059669
};

const CLICK_DRAG_TOLERANCE_PX = 6;

interface ThreeRuntime {
  scene: THREE.Scene;
  dynamicGroup: THREE.Group;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  controls: OrbitControls;
  clickable: THREE.Object3D[];
}

interface PointerGesture {
  pointerId: number;
  startX: number;
  startY: number;
  moved: boolean;
}

function to3D(x: number, y: number) {
  return new THREE.Vector3((x - 360) / 4.8, 0, (y - 280) / 4.8);
}

function resourceLayer(resource: PhysicalResource) {
  if (resource.resourceType === "camera" || resource.resourceType === "detector" || resource.resourceType === "simulator") {
    return "source";
  }
  if (resource.resourceType === "database" || resource.resourceType === "storage" || resource.resourceType === "controller") {
    return "destination";
  }
  return resource.direction === "input" ? "source" : "destination";
}

function resourceOffset(resource: PhysicalResource) {
  const n = Array.from(resource.id).reduce((sum, c) => sum + c.charCodeAt(0), 0);
  const angle = (n % 360) * Math.PI / 180;
  const radius = 16 + (n % 14);
  const layer = resourceLayer(resource);
  const vertical = layer === "source" ? -(22 + (n % 3) * 4) : 26 + (n % 4) * 4;
  return new THREE.Vector3(Math.cos(angle) * radius, vertical, Math.sin(angle) * radius);
}

function resourceColor(resource: PhysicalResource) {
  if (resource.resourceType === "camera" || resource.resourceType === "detector") return 0x16a34a;
  if (resource.resourceType === "database" || resource.resourceType === "storage") return 0x7c3aed;
  if (resource.resourceType === "controller") return 0xf59e0b;
  if (resource.resourceType === "simulator") return 0x0ea5e9;
  return resourceLayer(resource) === "source" ? 0x16a34a : 0x7c3aed;
}

function textSprite(text: string, color = "#0f172a") {
  const canvas = document.createElement("canvas");
  canvas.width = 360;
  canvas.height = 96;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.strokeStyle = "rgba(148,163,184,0.45)";
  ctx.lineWidth = 4;
  ctx.roundRect(10, 16, 340, 58, 16);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.font = "700 24px Microsoft YaHei, Arial";
  ctx.textAlign = "center";
  ctx.fillText(text, 180, 53);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }));
  sprite.scale.set(22, 6, 1);
  return sprite;
}

function addUserData(mesh: THREE.Object3D, kind: "node" | "resource", id: string) {
  mesh.userData = { kind, id };
  mesh.traverse((child) => {
    child.userData = { kind, id };
  });
}

function disposeMaterial(material: THREE.Material) {
  const maybeTextured = material as THREE.Material & { map?: THREE.Texture | null };
  maybeTextured.map?.dispose();
  material.dispose();
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const disposable = child as THREE.Object3D & {
      geometry?: THREE.BufferGeometry;
      material?: THREE.Material | THREE.Material[];
    };
    disposable.geometry?.dispose();
    if (Array.isArray(disposable.material)) {
      disposable.material.forEach(disposeMaterial);
    } else {
      disposable.material && disposeMaterial(disposable.material);
    }
  });
}

function clearDynamicGroup(group: THREE.Group) {
  const children = [...group.children];
  children.forEach((child) => {
    group.remove(child);
    disposeObject(child);
  });
}

function rebuildNetworkObjects(
  group: THREE.Group,
  snapshot: NetworkSnapshot,
  selected: SelectionRef | null,
  clickable: THREE.Object3D[]
) {
  clearDynamicGroup(group);
  clickable.length = 0;

  const nodePositions = new Map(snapshot.nodes.map((node) => [node.id, to3D(node.position.x, node.position.y)]));

  snapshot.edges.forEach((edge) => {
    const a = nodePositions.get(edge.source);
    const b = nodePositions.get(edge.target);
    if (!a || !b) return;
    const material = new THREE.LineBasicMaterial({
      color: edge.status === "warning" ? 0xd97706 : edge.status === "offline" ? 0x94a3b8 : 0xd97706,
      linewidth: 2
    });
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a.clone().setY(1.1), b.clone().setY(1.1)]), material);
    group.add(line);
  });

  snapshot.nodes.forEach((node) => {
    const pos = nodePositions.get(node.id)!;
    const nodeGroup = new THREE.Group();
    nodeGroup.position.copy(pos);
    const selectedNode = selected?.kind === "node" && selected.id === node.id;
    const body = new THREE.Mesh(
      new THREE.CylinderGeometry(selectedNode ? 5.6 : 4.6, selectedNode ? 5.6 : 4.6, 4.8, 32),
      new THREE.MeshStandardMaterial({ color: statusColor[node.status], roughness: 0.42, metalness: 0.08 })
    );
    body.position.y = 3.2;
    body.castShadow = true;
    nodeGroup.add(body);

    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(selectedNode ? 7.8 : 6.4, 0.34, 10, 48),
      new THREE.MeshStandardMaterial({ color: selectedNode ? 0x2563eb : 0xbfccdb, roughness: 0.5 })
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 0.7;
    nodeGroup.add(ring);

    const label = textSprite(node.label);
    label.position.set(0, 10.5, 0);
    nodeGroup.add(label);
    addUserData(nodeGroup, "node", node.id);
    clickable.push(nodeGroup);
    group.add(nodeGroup);
  });

  snapshot.resources.forEach((resource) => {
    const anchor = nodePositions.get(resource.anchorAgentId);
    if (!anchor) return;
    const offset = resourceOffset(resource);
    const pos = anchor.clone().add(offset);
    const layer = resourceLayer(resource);
    const lineMaterial = new THREE.LineBasicMaterial({
      color: layer === "source" ? 0x16a34a : 0x7c3aed,
      transparent: true,
      opacity: resource.status === "offline" ? 0.34 : 0.82
    });
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([anchor.clone().setY(4), pos]), lineMaterial));

    const resourceGroup = new THREE.Group();
    resourceGroup.position.copy(pos);
    const material = new THREE.MeshStandardMaterial({
      color: resourceColor(resource),
      roughness: 0.5,
      metalness: 0.04
    });
    let mesh: THREE.Mesh;
    if (resource.resourceType === "database" || resource.resourceType === "storage") {
      mesh = new THREE.Mesh(new THREE.CylinderGeometry(4.4, 4.4, 8, 32), material);
    } else if (resource.resourceType === "camera") {
      mesh = new THREE.Mesh(new THREE.ConeGeometry(4.6, 8, 24), material);
      mesh.rotation.z = layer === "source" ? 0 : Math.PI;
    } else {
      mesh = new THREE.Mesh(new THREE.BoxGeometry(8, 7, 8), material);
    }
    mesh.castShadow = true;
    resourceGroup.add(mesh);
    const label = textSprite(resource.label, "#111827");
    label.position.set(0, layer === "source" ? -8 : 9, 0);
    resourceGroup.add(label);
    addUserData(resourceGroup, "resource", resource.id);
    clickable.push(resourceGroup);
    group.add(resourceGroup);
  });
}

export function AgentNetwork3D({ snapshot, selected, onSelect }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<ThreeRuntime | null>(null);
  const onSelectRef = useRef(onSelect);
  const pointerGestureRef = useRef<PointerGesture | null>(null);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf6f9fc);

    const camera = new THREE.PerspectiveCamera(46, host.clientWidth / host.clientHeight, 0.1, 1000);
    camera.position.set(98, 86, 154);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(host.clientWidth, host.clientHeight);
    renderer.shadowMap.enabled = true;
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 2, 0);
    controls.minDistance = 70;
    controls.maxDistance = 280;
    controls.maxPolarAngle = Math.PI / 1.65;

    scene.add(new THREE.AmbientLight(0xffffff, 0.74));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(80, 160, 90);
    sun.castShadow = true;
    scene.add(sun);

    const plane = new THREE.Mesh(
      new THREE.PlaneGeometry(190, 145),
      new THREE.MeshStandardMaterial({ color: 0xe8f0f7, roughness: 0.9, metalness: 0, transparent: true, opacity: 0.7 })
    );
    plane.rotation.x = -Math.PI / 2;
    plane.receiveShadow = true;
    scene.add(plane);

    const sourceBand = new THREE.Mesh(
      new THREE.PlaneGeometry(185, 138),
      new THREE.MeshBasicMaterial({ color: 0x16a34a, transparent: true, opacity: 0.045, side: THREE.DoubleSide })
    );
    sourceBand.rotation.x = -Math.PI / 2;
    sourceBand.position.y = -28;
    scene.add(sourceBand);

    const destinationBand = new THREE.Mesh(
      new THREE.PlaneGeometry(185, 138),
      new THREE.MeshBasicMaterial({ color: 0x7c3aed, transparent: true, opacity: 0.055, side: THREE.DoubleSide })
    );
    destinationBand.rotation.x = -Math.PI / 2;
    destinationBand.position.y = 32;
    scene.add(destinationBand);

    const dynamicGroup = new THREE.Group();
    scene.add(dynamicGroup);
    const runtime: ThreeRuntime = { scene, dynamicGroup, camera, renderer, controls, clickable: [] };
    runtimeRef.current = runtime;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const selectFromPointer = (event: PointerEvent) => {
      const current = runtimeRef.current;
      if (!current) return;
      const rect = current.renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, current.camera);
      const hit = raycaster.intersectObjects(current.clickable, true)[0];
      const data = hit?.object.userData as Partial<SelectionRef> | undefined;
      if (data?.kind && data.id) {
        onSelectRef.current({ kind: data.kind, id: data.id });
      } else {
        onSelectRef.current(null);
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      pointerGestureRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        moved: false
      };
    };
    const onPointerMove = (event: PointerEvent) => {
      const gesture = pointerGestureRef.current;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      if (Math.hypot(dx, dy) > CLICK_DRAG_TOLERANCE_PX) {
        gesture.moved = true;
      }
    };
    const onPointerUp = (event: PointerEvent) => {
      const gesture = pointerGestureRef.current;
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      const moved = gesture.moved || Math.hypot(dx, dy) > CLICK_DRAG_TOLERANCE_PX;
      pointerGestureRef.current = null;
      if (!moved) selectFromPointer(event);
    };
    const onPointerCancel = (event: PointerEvent) => {
      if (pointerGestureRef.current?.pointerId === event.pointerId) {
        pointerGestureRef.current = null;
      }
    };
    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerup", onPointerUp);
    renderer.domElement.addEventListener("pointercancel", onPointerCancel);
    renderer.domElement.addEventListener("lostpointercapture", onPointerCancel);

    const onResize = () => {
      const width = Math.max(1, host.clientWidth);
      const height = Math.max(1, host.clientHeight);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
    };
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(host);
    window.addEventListener("resize", onResize);
    onResize();

    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      controls.update();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Sprite) obj.quaternion.copy(camera.quaternion);
      });
      renderer.render(scene, camera);
    };
    animate();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      renderer.domElement.removeEventListener("pointercancel", onPointerCancel);
      renderer.domElement.removeEventListener("lostpointercapture", onPointerCancel);
      runtimeRef.current = null;
      pointerGestureRef.current = null;
      controls.dispose();
      disposeObject(scene);
      renderer.dispose();
      if (renderer.domElement.parentElement === host) host.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    rebuildNetworkObjects(runtime.dynamicGroup, snapshot, selected, runtime.clickable);
  }, [selected, snapshot]);

  return (
    <section className="network3d-stage">
      <div ref={hostRef} className="three-host" />
      <div className="network3d-legend">
        <span><i className="legend-plane" />智能体平面关系</span>
        <span><i className="legend-down" />下方：来源</span>
        <span><i className="legend-up" />上方：发送目的地</span>
      </div>
    </section>
  );
}
