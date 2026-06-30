import { useEffect, useRef, useState } from "react";
import { fetchIntersection, IntersectionDetail } from "../api/agentNetworkClient";

// task5 P-10：单路口侧栏。点 per-junction agent → 拉该路口 ANP World Status，
// 渲染实时指标 + 各进口方向 + 前端轮询攒的历史曲线（冷路径无 TS DB，前端采样）。

const CONGESTION_COLOR: Record<string, string> = {
  "畅通": "#4c9c64", "缓行": "#d2b82f", "拥堵": "#e08b25", "严重": "#e05243",
};
const HISTORY_MAX = 60;

/** per-junction agent id（traffic-{perception,exec}-sv-j<jid>）→ 路口 id。 */
function junctionIdOf(agentId: string): string {
  return agentId.replace(/^traffic-(perception|exec)-sv-j/, "");
}

interface Props {
  agentId: string;
  onClose: () => void;
}

export function IntersectionDetailPanel({ agentId, onClose }: Props) {
  const jid = junctionIdOf(agentId);
  const [detail, setDetail] = useState<IntersectionDetail | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [, setTick] = useState(0);
  const history = useRef<{ queue: number; speed: number }[]>([]);

  useEffect(() => {
    history.current = [];
    setLoaded(false);
    let alive = true;
    const load = async () => {
      const d = await fetchIntersection(jid);
      if (!alive) return;
      setDetail(d);
      setLoaded(true);
      if (d) {
        history.current = [...history.current, { queue: d.queue_length_m, speed: d.mean_speed_kmh }].slice(-HISTORY_MAX);
        setTick((t) => t + 1);
      }
    };
    load();
    const id = window.setInterval(load, 1500);
    return () => { alive = false; window.clearInterval(id); };
  }, [jid]);

  const hist = history.current;
  const W = 320, H = 84;
  const maxQ = Math.max(10, ...hist.map((h) => h.queue));
  const queuePts = hist
    .map((h, i) => `${(i / Math.max(1, HISTORY_MAX - 1)) * W},${(H - (h.queue / maxQ) * H).toFixed(1)}`)
    .join(" ");

  return (
    <aside className="intersection-detail-panel" aria-label="单路口详情">
      <header>
        <div>
          <div className="idp-kicker">路口 World Status</div>
          <h3>路口 {jid}</h3>
        </div>
        <button onClick={onClose} title="关闭">×</button>
      </header>

      {!loaded ? (
        <div className="idp-empty">加载中…</div>
      ) : !detail ? (
        <div className="idp-empty">该路口暂无 World Status（仿真未覆盖或已停）</div>
      ) : (
        <>
          <section className="idp-block">
            <h4>实时交通指标</h4>
            <div className="idp-grid">
              <div><label>排队长度</label><b>{detail.queue_length_m.toFixed(0)} m</b></div>
              <div><label>流量</label><b>{detail.flow_veh_h.toFixed(0)} 辆/h</b></div>
              <div><label>均速</label><b>{detail.mean_speed_kmh.toFixed(1)} km/h</b></div>
              <div><label>延误</label><b>{detail.mean_delay_sec.toFixed(1)} s</b></div>
              <div className="idp-wide">
                <label>拥堵级别</label>
                <b style={{ color: CONGESTION_COLOR[detail.congestion_level] || "#adbac7" }}>
                  {detail.congestion_level}（{(detail.congestion_index * 100).toFixed(0)}%）
                </b>
              </div>
            </div>
          </section>

          {detail.approaches.length > 0 && (
            <section className="idp-block">
              <h4>各进口方向</h4>
              <table className="idp-table">
                <thead><tr><th>方向</th><th>排队(m)</th><th>流量</th><th>均速</th></tr></thead>
                <tbody>
                  {detail.approaches.map((a) => (
                    <tr key={a.direction}>
                      <td>{a.direction}</td>
                      <td>{a.queue_length_m.toFixed(0)}</td>
                      <td>{a.flow_veh_h.toFixed(0)}</td>
                      <td>{a.mean_speed_kmh.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          <section className="idp-block">
            <h4>历史监测窗口 <small>最近 {hist.length} 帧 · 排队长度</small></h4>
            <svg className="idp-history" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
              {hist.length > 1 && <polyline points={queuePts} fill="none" stroke="#3fb991" strokeWidth="2" />}
            </svg>
          </section>
        </>
      )}
    </aside>
  );
}
