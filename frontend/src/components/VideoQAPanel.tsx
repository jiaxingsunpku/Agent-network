import { FormEvent, useEffect, useRef, useState } from "react";
import { AlertCircle, Camera, ChevronDown, FileText, ListChecks, Loader2, Search } from "lucide-react";
import { queryVideoText, VideoQueryResponse } from "../api/videoTextClient";
import { LocationCameraPicker, LocationSelection } from "./LocationCameraPicker";

// 视频事件问答面板（P7 → P9）：按日期/时间/路段提问 → 检索视频文本库 → 展示回答 + 命中证据。
// P9：升为「监控世界模型」主界面（variant="main"，全高聊天）；并支持把协作任务的聚合答案
// 回灌主聊天（injected）。variant="panel" 保留原紧凑可折叠形态。样式按现有 ANP 前端。

const SAMPLE_QUESTION = "6月13号下午民族大道有没有事故？";

export interface InjectedResult {
  title: string;
  result: VideoQueryResponse;
}

interface Props {
  variant?: "panel" | "main";
  injected?: InjectedResult | null;
  /** 点击命中证据时回调 event_id（用于跳转事件数据库视图并定位高亮）。 */
  onOpenEvidence?: (eventId: string) => void;
}

function fmtTime(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

function ResultView({
  result,
  banner,
  onOpenEvidence
}: {
  result: VideoQueryResponse;
  banner?: string;
  onOpenEvidence?: (eventId: string) => void;
}) {
  const warningText = result.warnings?.filter(Boolean).join("；");
  return (
    <div className="video-qa-result">
      {banner && (
        <div className="video-qa-task-banner">
          <ListChecks size={13} />
          <span>{banner}</span>
        </div>
      )}
      <div className="video-qa-answer">{result.answer}</div>
      {warningText && <div className="video-qa-warnings">{warningText}</div>}
      <div className="video-qa-evidence-head">
        命中证据 {result.evidence.length} 条{onOpenEvidence && result.evidence.length > 0 ? "（点击查看数据库记录）" : ""}
      </div>
      <ul className="video-qa-evidence">
        {result.evidence.map((ev) => {
          const inner = (
            <>
              <div className="video-qa-evi-meta">
                <span className="evi-time">{fmtTime(ev.event_ts)}</span>
                {ev.road_name && <span className="evi-road">{ev.road_name}</span>}
                {ev.category && <span className="evi-cat">{ev.category}</span>}
                {ev.camera_id && (
                  <span className="evi-cam">
                    <Camera size={11} />
                    {ev.camera_id}
                  </span>
                )}
                {typeof ev.confidence === "number" && (
                  <span className="evi-conf">置信度 {(ev.confidence * 100).toFixed(0)}%</span>
                )}
              </div>
              <div className="video-qa-evi-text">{ev.summary || ev.text}</div>
            </>
          );
          return onOpenEvidence ? (
            <li key={ev.event_id} className="video-qa-evi-li">
              <button
                type="button"
                className="video-qa-evi video-qa-evi--clickable"
                onClick={() => onOpenEvidence(ev.event_id)}
                title="在事件数据库中查看此记录"
              >
                {inner}
              </button>
            </li>
          ) : (
            <li key={ev.event_id} className="video-qa-evi">
              {inner}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function VideoQAPanel({ variant = "panel", injected = null, onOpenEvidence }: Props) {
  const isMain = variant === "main";
  const [expanded, setExpanded] = useState(isMain);
  const [question, setQuestion] = useState(SAMPLE_QUESTION);
  const [loc, setLoc] = useState<LocationSelection>({});
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VideoQueryResponse | null>(null);
  // 当前展示：本地问答结果（query）或来自任务的聚合结果（task）。
  const [mode, setMode] = useState<"query" | "task">("query");
  const lastInjected = useRef<string | null>(null);

  // 协作任务结果回灌：injected 变化时切到 task 视图展示其聚合答案。
  useEffect(() => {
    if (injected && injected.title !== lastInjected.current) {
      lastInjected.current = injected.title;
      setMode("task");
      setExpanded(true);
    }
  }, [injected]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setExpanded(true);
    setMode("query");
    setLoading(true);
    setError(null);
    try {
      const res = await queryVideoText({
        question: question.trim(),
        intersection_id: loc.intersection_id || undefined,
        camera_id: loc.camera_id || undefined,
        road_name: loc.road_name || undefined,
        time_from: timeFrom ? new Date(timeFrom).toISOString() : undefined,
        time_to: timeTo ? new Date(timeTo).toISOString() : undefined
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const shown = mode === "task" && injected ? injected.result : result;
  const banner = mode === "task" && injected ? `来自协作任务：${injected.title}` : undefined;
  const evidenceCount = shown?.evidence?.length ?? 0;

  const form = (
    <form className="video-qa-form" onSubmit={onSubmit}>
      <textarea
        className="video-qa-question"
        rows={2}
        placeholder="6月13号下午民族大道有没有事故？"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />
      <LocationCameraPicker value={loc} onChange={setLoc} />
      <div className="video-qa-filters">
        <label>
          起
          <input type="datetime-local" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} />
        </label>
        <label>
          止
          <input type="datetime-local" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} />
        </label>
        <button type="submit" className="video-qa-submit" disabled={loading}>
          {loading ? <Loader2 size={15} /> : <Search size={15} />}
          <span>{loading ? "检索中" : "提问"}</span>
        </button>
      </div>
    </form>
  );

  const body = (
    <div className="video-qa-body">
      {form}
      {error && (
        <div className="video-qa-error">
          <AlertCircle size={15} />
          <span>请求失败：{error}</span>
        </div>
      )}
      {shown && <ResultView result={shown} banner={banner} onOpenEvidence={onOpenEvidence} />}
      {!shown && !error && (
        <div className="video-qa-empty">提问以监控视频世界模型，或在右侧新建协作任务、点任务把聚合结果回灌这里。</div>
      )}
    </div>
  );

  if (isMain) {
    return (
      <section className="video-qa video-qa--main">
        <header className="video-qa-summary" aria-label="视频事件问答主界面">
          <span className="video-qa-title">
            <FileText size={15} />
            <b>视频世界模型 · 事件问答</b>
          </span>
          <span className="video-qa-meta">{shown ? `证据 ${evidenceCount}` : "监控主界面"}</span>
        </header>
        {body}
      </section>
    );
  }

  return (
    <details className="video-qa" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="video-qa-summary">
        <span className="video-qa-title">
          <FileText size={15} />
          <b>视频事件问答</b>
        </span>
        <span className="video-qa-meta">{shown ? `证据 ${evidenceCount}` : "video-text"}</span>
        <ChevronDown className="video-qa-chevron" size={16} />
      </summary>
      {body}
    </details>
  );
}
