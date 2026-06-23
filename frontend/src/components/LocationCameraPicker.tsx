import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Camera, ChevronDown, MapPin, X } from "lucide-react";
import { IntersectionFacet, listLocations } from "../api/videoTextClient";

// 可复用「位置选择器」（task2）：路口可搜索下拉(combobox) + 摄像头下拉(首项「所有摄像头」)。
// 受控组件：选择回调 { intersection_id, road_name, camera_id? }。选项从 ANP 文本库派生
// （GET /locations），按 vision hub「路口→摄像头(方位)」层级组织。切路口自动把摄像头重置为
// 「所有」（不在 effect 里做，避免无限渲染——见 selectIntersection）。

export interface LocationSelection {
  intersection_id?: string;
  road_name?: string;
  camera_id?: string;
}

interface Props {
  value: LocationSelection;
  onChange: (next: LocationSelection) => void;
  /** 紧凑布局（侧栏用）：路口 + 摄像头单列堆叠 */
  compact?: boolean;
  labels?: { intersection?: string; camera?: string };
}

/** 路口展示标签：道路名（有则）+ intersection_id；优先 intersection_name。 */
function intersectionLabel(i: IntersectionFacet): string {
  const name = i.intersection_name || i.road_name || "";
  const id = i.intersection_id || "";
  if (name && id) return `${name} · ${id}`;
  return name || id || "未标注路口";
}

/** 每个路口的稳定 key（intersection_id 优先；为空时按 road 兜底）。 */
function intersectionKey(i: IntersectionFacet): string {
  return i.intersection_id || (i.road_name ? `road:${i.road_name}` : "__ungrouped__");
}

/** 从 camera_id 词缀启发式派生方位标签（仅展示用；匹配仍用 camera_id 精确值）。 */
export function cameraPositionLabel(cameraId?: string | null): string {
  if (!cameraId) return "";
  const s = cameraId.toLowerCase();
  if (/(east|东)/.test(s)) return "东进口";
  if (/(west|西)/.test(s)) return "西进口";
  if (/(north|北)/.test(s)) return "北进口";
  if (/(south|南)/.test(s)) return "南进口";
  return "";
}

export function LocationCameraPicker({ value, onChange, compact = false, labels }: Props) {
  const [intersections, setIntersections] = useState<IntersectionFacet[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    listLocations()
      .then((res) => {
        if (!alive) return;
        setIntersections(res.intersections);
        setLoadError(null);
      })
      .catch((err) => {
        if (!alive) return;
        setLoadError(err instanceof Error ? err.message : String(err));
        setIntersections([]);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
      if (blurTimer.current) window.clearTimeout(blurTimer.current);
    };
  }, []);

  const selected = useMemo(() => {
    if (value.intersection_id) {
      return intersections.find((i) => i.intersection_id === value.intersection_id) ?? null;
    }
    if (value.road_name) {
      return intersections.find((i) => !i.intersection_id && i.road_name === value.road_name) ?? null;
    }
    return null;
  }, [intersections, value.intersection_id, value.road_name]);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return intersections;
    return intersections.filter((i) => {
      const hay = `${intersectionLabel(i)} ${i.intersection_id || ""} ${i.road_name || ""}`.toLowerCase();
      return hay.includes(term);
    });
  }, [intersections, query]);

  function selectIntersection(i: IntersectionFacet | null) {
    // 切路口 → 摄像头重置为「所有」(camera_id undefined)。这里同步做，不放 effect，避免循环。
    if (!i) {
      onChange({});
    } else {
      onChange({
        intersection_id: i.intersection_id || undefined,
        road_name: i.road_name || undefined,
        camera_id: undefined
      });
    }
    setOpen(false);
    setQuery("");
  }

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && filtered.length > 0) selectIntersection(filtered[0]);
    } else if (e.key === "ArrowDown" && !open) {
      setOpen(true);
    }
  }

  const cameras = selected?.cameras ?? [];
  const empty = !loading && intersections.length === 0;
  const display = open ? query : selected ? intersectionLabel(selected) : "";
  const interLabel = labels?.intersection ?? "路口";
  const camLabel = labels?.camera ?? "摄像头";

  return (
    <div className={`location-picker${compact ? " location-picker--compact" : ""}`}>
      <div className="lp-field">
        <span className="lp-label">
          <MapPin size={12} />
          {interLabel}
        </span>
        <div className="lp-combobox">
          <input
            className="lp-input"
            type="text"
            value={display}
            disabled={empty}
            placeholder={
              empty
                ? "库内暂无事件，先回放/接入数据"
                : selected
                  ? intersectionLabel(selected)
                  : loading
                    ? "加载路口…"
                    : "搜索路口…"
            }
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => {
              if (empty) return;
              setQuery("");
              setOpen(true);
            }}
            onBlur={() => {
              blurTimer.current = window.setTimeout(() => setOpen(false), 130);
            }}
            onKeyDown={onInputKeyDown}
            aria-label={interLabel}
          />
          {selected && !empty && (
            <button
              type="button"
              className="lp-clear"
              title="清除路口（不限）"
              onMouseDown={(e) => {
                e.preventDefault();
                selectIntersection(null);
              }}
            >
              <X size={13} />
            </button>
          )}
          <ChevronDown className="lp-chevron" size={15} />
          {open && !empty && (
            <ul className="lp-menu" role="listbox">
              <li
                className="lp-option lp-option--any"
                role="option"
                aria-selected={!selected}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectIntersection(null);
                }}
              >
                不限路口（全部）
              </li>
              {filtered.length === 0 && <li className="lp-option lp-empty">无匹配路口</li>}
              {filtered.map((i) => {
                const key = intersectionKey(i);
                const active = selected ? intersectionKey(selected) === key : false;
                return (
                  <li
                    key={key}
                    className={`lp-option${active ? " active" : ""}`}
                    role="option"
                    aria-selected={active}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      selectIntersection(i);
                    }}
                  >
                    <span className="lp-option-name">{intersectionLabel(i)}</span>
                    <span className="lp-option-count">{i.event_count}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <div className="lp-field">
        <span className="lp-label">
          <Camera size={12} />
          {camLabel}
        </span>
        <select
          className="lp-camera"
          value={value.camera_id ?? ""}
          disabled={!selected || cameras.length === 0}
          onChange={(e) => onChange({ ...value, camera_id: e.target.value || undefined })}
          aria-label={camLabel}
        >
          <option value="">所有摄像头（该路口全部）</option>
          {cameras.map((c) => {
            // 目录来源优先用真实 camera_position；否则回退 camera_id 词缀启发式。
            const pos = c.camera_position || cameraPositionLabel(c.camera_id);
            const tail = c.source_id != null ? `#${c.source_id}` : c.camera_id;
            const label = [pos, tail].filter(Boolean).join(" · ");
            return (
              <option
                key={c.source_id ?? c.camera_id}
                value={c.camera_id}
                title={c.name ?? undefined}
              >
                {label}（{c.event_count}）
              </option>
            );
          })}
        </select>
      </div>

      {loadError && <div className="lp-error">位置加载失败：{loadError}</div>}
    </div>
  );
}
