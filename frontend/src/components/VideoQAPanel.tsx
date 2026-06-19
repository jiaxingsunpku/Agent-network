import { FormEvent, useState } from "react";
import { queryVideoText, VideoQueryResponse } from "../api/videoTextClient";

// 视频事件问答面板（P7）：按日期/时间/路段提问 → 检索视频文本库 → 展示回答 + 命中证据。
// 借鉴旧项目「answer + evidence」展示思想，样式按现有 ANP 前端。需网关挂载 video-text 路由。

const SAMPLE_QUESTION = "6月13号下午民族大道有没有事故？";

function fmtTime(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function VideoQAPanel() {
  const [question, setQuestion] = useState(SAMPLE_QUESTION);
  const [roadName, setRoadName] = useState("民族大道");
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VideoQueryResponse | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await queryVideoText({
        question: question.trim(),
        road_name: roadName.trim() || undefined,
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

  return (
    <details className="video-qa" open>
      <summary className="video-qa-summary">视频事件问答 · video-text</summary>
      <div className="video-qa-body">
        <form className="video-qa-form" onSubmit={onSubmit}>
          <textarea
            className="video-qa-question"
            rows={2}
            placeholder="按日期/时间/路段提问，如：6月13号下午民族大道有没有事故？"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="video-qa-filters">
            <label>
              路段
              <input value={roadName} onChange={(e) => setRoadName(e.target.value)} placeholder="如 民族大道" />
            </label>
            <label>
              起
              <input type="datetime-local" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} />
            </label>
            <label>
              止
              <input type="datetime-local" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} />
            </label>
            <button type="submit" className="video-qa-submit" disabled={loading}>
              {loading ? "检索中…" : "提问"}
            </button>
          </div>
        </form>

        {error && <div className="video-qa-error">请求失败：{error}（网关是否已挂载 video-text 路由？）</div>}

        {result && (
          <div className="video-qa-result">
            <div className="video-qa-answer">{result.answer}</div>
            {result.warnings.length > 0 && (
              <div className="video-qa-warnings">{result.warnings.join("；")}</div>
            )}
            <div className="video-qa-evidence-head">命中证据 {result.evidence.length} 条</div>
            <ul className="video-qa-evidence">
              {result.evidence.map((ev) => (
                <li key={ev.event_id} className="video-qa-evi">
                  <div className="video-qa-evi-meta">
                    <span className="evi-time">{fmtTime(ev.event_ts)}</span>
                    {ev.road_name && <span className="evi-road">{ev.road_name}</span>}
                    {ev.category && <span className="evi-cat">{ev.category}</span>}
                    {ev.camera_id && <span className="evi-cam">{ev.camera_id}</span>}
                    {typeof ev.confidence === "number" && (
                      <span className="evi-conf">置信度 {(ev.confidence * 100).toFixed(0)}%</span>
                    )}
                  </div>
                  <div className="video-qa-evi-text">{ev.summary || ev.text}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}
