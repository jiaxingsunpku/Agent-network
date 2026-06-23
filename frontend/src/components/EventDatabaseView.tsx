import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ChevronLeft, ChevronRight, Database, Loader2, RefreshCw, Search, X } from "lucide-react";
import {
  browseEvents,
  EventBrowseResponse,
  EventRecord,
  getEvent
} from "../api/videoTextClient";
import { cameraPositionLabel, LocationCameraPicker, LocationSelection } from "./LocationCameraPicker";

// 事件数据库视图（task2）：分页 + 筛选浏览 ANP 文本库（SQLite video_text_events）记录，
// 行点击看详情（全文/标签/实体/artifact 指针/envelope）。支持外部 focusEventId 自动打开
// 并定位高亮（问答证据点击跳转入口）。展示的是 ANP 文本库，不是 vision hub 底层库；
// artifact 只展示轻指针，原始视频字节由前端直连 vision hub 取（本视图不实现）。

const PAGE_SIZE = 20;
const CATEGORY_OPTIONS = ["", "事故", "拥堵", "违章", "施工", "积水"];

interface Props {
  /** 外部要求定位的记录；nonce 保证重复点同一条也重新触发定位。 */
  focusEventId?: { id: string; nonce: number } | null;
}

function fmtTime(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function EventDatabaseView({ focusEventId = null }: Props) {
  const [loc, setLoc] = useState<LocationSelection>({});
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<EventBrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [detail, setDetail] = useState<EventRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const pendingScroll = useRef(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await browseEvents({
        intersection_id: loc.intersection_id,
        camera_id: loc.camera_id,
        road_name: loc.road_name,
        category: category || undefined,
        q: q.trim() || undefined,
        limit: PAGE_SIZE,
        offset
      });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [loc, category, q, offset]);

  useEffect(() => {
    reload();
  }, [reload]);

  // 外部跳转定位：取详情 + 高亮 + 滚动到视图（不在当前页也至少打开详情）。
  useEffect(() => {
    if (!focusEventId) return;
    let alive = true;
    setDetailLoading(true);
    setFocusedId(focusEventId.id);
    pendingScroll.current = true;
    getEvent(focusEventId.id)
      .then((rec) => alive && setDetail(rec))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => alive && setDetailLoading(false));
    return () => {
      alive = false;
    };
  }, [focusEventId]);

  async function openDetail(eventId: string) {
    setFocusedId(eventId);
    setDetailLoading(true);
    try {
      setDetail(await getEvent(eventId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDetailLoading(false);
    }
  }

  function updateFilter(next: () => void) {
    next();
    setOffset(0);
  }

  const total = data?.total ?? 0;
  const items = data?.items ?? [];
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);
  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;

  return (
    <section className="event-db" aria-label="事件数据库">
      <div className="event-db-toolbar">
        <LocationCameraPicker value={loc} onChange={(v) => updateFilter(() => setLoc(v))} />
        <label className="event-db-filter">
          <span>类别</span>
          <select value={category} onChange={(e) => updateFilter(() => setCategory(e.target.value))}>
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c || "all"} value={c}>
                {c || "全部类别"}
              </option>
            ))}
          </select>
        </label>
        <label className="event-db-filter event-db-filter--grow">
          <span>关键词</span>
          <div className="event-db-search">
            <Search size={13} />
            <input
              value={q}
              placeholder="正文 / 摘要 / 路名…"
              onChange={(e) => updateFilter(() => setQ(e.target.value))}
            />
          </div>
        </label>
        <button type="button" className="event-db-refresh" onClick={reload} title="刷新">
          {loading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
        </button>
      </div>

      {error && (
        <div className="event-db-error">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      <div className="event-db-tablewrap">
        <table className="event-db-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>路口 / 道路</th>
              <th>摄像头</th>
              <th>类别</th>
              <th>置信度</th>
              <th>摘要</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && (
              <tr>
                <td className="event-db-empty" colSpan={6}>
                  {total === 0 ? "库内暂无匹配记录。可调整筛选，或先回放/接入视频文本事件。" : "本页无记录。"}
                </td>
              </tr>
            )}
            {items.map((ev) => {
              const pos = cameraPositionLabel(ev.camera_id);
              const isFocused = focusedId === ev.event_id;
              return (
                <tr
                  key={ev.event_id}
                  className={`event-db-row${isFocused ? " focused" : ""}`}
                  onClick={() => openDetail(ev.event_id)}
                  ref={(el) => {
                    if (el && isFocused && pendingScroll.current) {
                      el.scrollIntoView({ block: "center", behavior: "smooth" });
                      pendingScroll.current = false;
                    }
                  }}
                >
                  <td className="event-db-td-time">{fmtTime(ev.event_ts)}</td>
                  <td>
                    <div className="event-db-loc">{ev.intersection_id || ev.road_name || "—"}</div>
                    {ev.intersection_id && ev.road_name && <div className="event-db-loc-sub">{ev.road_name}</div>}
                  </td>
                  <td>
                    <div className="event-db-cam">{ev.camera_id || "—"}</div>
                    {pos && <div className="event-db-loc-sub">{pos}</div>}
                  </td>
                  <td>{ev.category ? <span className="event-db-cat">{ev.category}</span> : "—"}</td>
                  <td>{typeof ev.confidence === "number" ? `${(ev.confidence * 100).toFixed(0)}%` : "—"}</td>
                  <td className="event-db-td-summary">{ev.summary || ev.text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="event-db-pager">
        <span className="event-db-count">
          {total > 0 ? `${from}–${to} / 共 ${total} 条` : "共 0 条"}
        </span>
        <div className="event-db-pager-btns">
          <button type="button" disabled={!canPrev} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            <ChevronLeft size={14} />
            上一页
          </button>
          <button type="button" disabled={!canNext} onClick={() => setOffset(offset + PAGE_SIZE)}>
            下一页
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      {(detail || detailLoading) && (
        <>
          <div className="event-db-backdrop" onClick={() => setDetail(null)} />
          <aside className="event-db-drawer" aria-label="事件详情">
            <div className="event-db-drawer-head">
              <span>
                <Database size={14} />
                事件详情
              </span>
              <button type="button" className="event-db-close" onClick={() => setDetail(null)} title="关闭">
                <X size={15} />
              </button>
            </div>
            {detailLoading && !detail && (
              <div className="event-db-drawer-loading">
                <Loader2 size={16} className="spin" /> 加载中…
              </div>
            )}
            {detail && (
              <div className="event-db-drawer-body">
                <dl className="event-db-kv">
                  <dt>事件 ID</dt>
                  <dd className="mono">{detail.event_id}</dd>
                  <dt>时间</dt>
                  <dd>{fmtTime(detail.event_ts)}</dd>
                  <dt>路口</dt>
                  <dd>{detail.intersection_id || "—"}</dd>
                  <dt>道路</dt>
                  <dd>{detail.road_name || "—"}</dd>
                  <dt>摄像头</dt>
                  <dd>
                    {detail.camera_id || "—"}
                    {cameraPositionLabel(detail.camera_id) ? `（${cameraPositionLabel(detail.camera_id)}）` : ""}
                  </dd>
                  <dt>类别</dt>
                  <dd>{detail.category || "—"}</dd>
                  <dt>置信度</dt>
                  <dd>{typeof detail.confidence === "number" ? `${(detail.confidence * 100).toFixed(0)}%` : "—"}</dd>
                  <dt>来源模型</dt>
                  <dd>{detail.source_model || "—"}</dd>
                  <dt>来源体</dt>
                  <dd className="mono">{detail.source_agent_id || "—"}</dd>
                  {detail.parent_trace_id && (
                    <>
                      <dt>归因命令</dt>
                      <dd className="mono">{detail.parent_trace_id}</dd>
                    </>
                  )}
                </dl>

                <div className="event-db-section-title">全文</div>
                <div className="event-db-fulltext">{detail.text}</div>

                {detail.summary && (
                  <>
                    <div className="event-db-section-title">摘要</div>
                    <div className="event-db-fulltext">{detail.summary}</div>
                  </>
                )}

                {detail.tags.length > 0 && (
                  <>
                    <div className="event-db-section-title">标签</div>
                    <div className="event-db-tags">
                      {detail.tags.map((t) => (
                        <span key={t} className="event-db-tag">
                          {t}
                        </span>
                      ))}
                    </div>
                  </>
                )}

                {Object.keys(detail.entities || {}).length > 0 && (
                  <>
                    <div className="event-db-section-title">实体</div>
                    <dl className="event-db-kv">
                      {Object.entries(detail.entities).map(([k, v]) => (
                        <span key={k} className="event-db-kv-pair">
                          <dt>{k}</dt>
                          <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                        </span>
                      ))}
                    </dl>
                  </>
                )}

                <div className="event-db-section-title">媒体指针（artifact_ref）</div>
                <div className="event-db-artifact">
                  <code>{detail.artifact_ref || "无"}</code>
                  <div className="event-db-artifact-note">
                    仅轻指针。原始视频/帧不入 ANP，需要字节请由前端直连 vision hub 取。
                  </div>
                </div>

                {detail.envelope && (
                  <details className="event-db-envelope">
                    <summary>原始 envelope（JSON）</summary>
                    <pre>{JSON.stringify(detail.envelope, null, 2)}</pre>
                  </details>
                )}
              </div>
            )}
          </aside>
        </>
      )}
    </section>
  );
}
