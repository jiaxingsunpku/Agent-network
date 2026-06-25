import { ChevronRight, GitBranch, Globe } from "lucide-react";

export interface ToolbarItem {
  id: string;
  name: string;
  subtitle: string;
  status?: string;
}

interface Props {
  items: ToolbarItem[];
  activeId: string;
  onSelect: (id: string) => void;
  onSelectWorld?: () => void;
  worldActive?: boolean;
}

const STATUS_DOT: Record<string, string> = {
  online: "#1d9e75",
  warning: "#ba7517",
  degraded: "#ba7517",
  offline: "#888780",
  syncing: "#378add"
};

export function LeftToolbar({ items, activeId, onSelect, onSelectWorld, worldActive }: Props) {
  return (
    <aside className="left-rail slim-left-rail">
      <section className="rail-section world-model-only-section">
        {onSelectWorld && (
          <button
            className={worldActive ? "entity-row world-model-row active" : "entity-row world-model-row"}
            onClick={onSelectWorld}
            style={{ marginBottom: 10 }}
          >
            <Globe size={18} />
            <span>
              <b>世界总览</b>
              <small>统一世界 · 全部 agent</small>
            </span>
            <ChevronRight size={16} />
          </button>
        )}
        <div className="section-title">
          <GitBranch size={18} />
          世界模型
        </div>
        <div className="entity-list world-model-list simplified">
          {items.map((item) => (
            <button
              key={item.id}
              className={!worldActive && activeId === item.id ? "entity-row world-model-row active" : "entity-row world-model-row"}
              onClick={() => onSelect(item.id)}
            >
              <GitBranch size={18} />
              <span>
                <b>
                  {item.status && (
                    <i style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: STATUS_DOT[item.status] ?? "#888780", marginRight: 6, verticalAlign: "middle" }} />
                  )}
                  {item.name}
                </b>
                <small>{item.subtitle}</small>
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
          {items.length === 0 && <div style={{ padding: "8px 10px", fontSize: 12, opacity: 0.5 }}>（世界里暂无 model）</div>}
        </div>
      </section>
    </aside>
  );
}
