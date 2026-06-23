import { useRef, useState } from "react";
import { Database, MessageSquareText } from "lucide-react";
import { InjectedResult, VideoQAPanel } from "./VideoQAPanel";
import { TaskSidebar } from "./TaskSidebar";
import { EventDatabaseView } from "./EventDatabaseView";

// 视频世界模型监控视图（P9 → task2，仅 ?source=gateway）：
// 视图切换 Tab：「事件问答」（问答主界面 + 协作任务侧栏）/「事件数据库」（浏览 ANP 文本库）。
// 问答证据可点 → 切到数据库视图并定位高亮该 event_id（focusEvent 用 {id, nonce}，nonce 保证
// 重复点同一条也重新触发定位）。ANP 只下命令/收文本/做聚合，原始视频不进 ANP。
type ActiveView = "qa" | "database";

export function VideoWorldModelView() {
  const [injected, setInjected] = useState<InjectedResult | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>("qa");
  const [focusEvent, setFocusEvent] = useState<{ id: string; nonce: number } | null>(null);
  const nonce = useRef(0);

  function openEvidence(eventId: string) {
    nonce.current += 1;
    setFocusEvent({ id: eventId, nonce: nonce.current });
    setActiveView("database");
  }

  return (
    <div className="video-wm-shell">
      <div className="video-wm-tabs" role="tablist" aria-label="视频世界模型视图切换">
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "qa"}
          className={`video-wm-tab${activeView === "qa" ? " active" : ""}`}
          onClick={() => setActiveView("qa")}
        >
          <MessageSquareText size={14} />
          事件问答
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeView === "database"}
          className={`video-wm-tab${activeView === "database" ? " active" : ""}`}
          onClick={() => setActiveView("database")}
        >
          <Database size={14} />
          事件数据库
        </button>
      </div>

      {activeView === "qa" ? (
        <div className="video-wm">
          <div className="video-wm-main">
            <VideoQAPanel variant="main" injected={injected} onOpenEvidence={openEvidence} />
          </div>
          <TaskSidebar onPickResult={setInjected} />
        </div>
      ) : (
        <div className="video-wm-db">
          <EventDatabaseView focusEventId={focusEvent} />
        </div>
      )}
    </div>
  );
}
