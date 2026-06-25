import { useEffect, useState } from "react";
import { fetchWorld, WorldView } from "../api/agentNetworkClient";

/** 在 gateway 模式下每 3s 拉一次统一世界 /world；未启用或不可达时为 null。 */
export function useWorld(enabled: boolean): WorldView | null {
  const [world, setWorld] = useState<WorldView | null>(null);

  useEffect(() => {
    if (!enabled) {
      setWorld(null);
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const next = await fetchWorld();
        if (!cancelled && next) setWorld(next);
      } catch {
        /* 保留上一帧，避免闪烁 */
      }
    };
    load();
    const id = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled]);

  return world;
}
