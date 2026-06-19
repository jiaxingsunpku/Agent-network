"""视频文本检索问答（P7 一阶段）。

链路：问题 → 启发式补全过滤 → store 检索（关键词+时空）→ 合成回答。
合成默认接 GLM（RAG：把命中文本喂给 LLM 让其据实归纳、带引用）；无 key 或 LLM
报错时回退**规则摘要**（按时间排序归纳）。响应外形对齐老前端 ``QueryResponse``。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any

from anp.contracts import parse_iso

from .config import LLMConfig
from .llm import LLMError, chat
from .retrieval import SearchFilters, extract_filters
from .store import VideoTextStore

_SYSTEM_PROMPT = (
    "你是 ANP 视频事件问答助手。只能依据提供的『视频文本事件』作答，"
    "不得编造未出现的事实或数字。用简体中文、简洁回答，并在结论中引用关键证据"
    "（时间、路段/路口、摄像头）。事件时间已按**北京时间**给出，"
    "判断『上午/下午/早晚』等时段请直接依据北京时间。"
    "若证据不足以回答，明说『未检索到相关记录』。"
)


def _beijing(ts: str | None) -> str:
    """事件时间（UTC ISO）→ 北京时间可读串，供 LLM 正确判断时段。"""

    if not ts:
        return "未知时间"
    try:
        return (parse_iso(ts) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") + "（北京时间）"
    except Exception:  # noqa: BLE001
        return ts


def _evidence_item(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": rec.get("event_id"),
        "event_ts": rec.get("event_ts"),
        "camera_id": rec.get("camera_id"),
        "road_name": rec.get("road_name"),
        "intersection_id": rec.get("intersection_id"),
        "category": rec.get("category"),
        "summary": rec.get("summary"),
        "text": rec.get("text", ""),
        "confidence": rec.get("confidence"),
        "artifact_ref": rec.get("artifact_ref"),
    }


def _filters_to_args(f: SearchFilters) -> dict[str, Any]:
    d = asdict(f)
    return {k: v for k, v in d.items() if v not in (None, [], "")}


def _format_hits_for_llm(records: list[dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(records, 1):
        loc = r.get("road_name") or r.get("intersection_id") or r.get("road_segment") or "未知路段"
        cam = r.get("camera_id") or "未知摄像头"
        cat = f"[{r['category']}]" if r.get("category") else ""
        body = r.get("summary") or r.get("text") or ""
        lines.append(f"{i}. {_beijing(r.get('event_ts'))} | {loc} | {cam} {cat} {body}".strip())
    return "\n".join(lines)


def rule_summary(question: str, records: list[dict[str, Any]]) -> str:
    """规则摘要兜底：按时间倒序归纳命中事件（store 已按 event_ts DESC 返回）。"""

    n = len(records)
    head = f"在检索范围内共找到 {n} 条相关视频文本事件，按时间从近到远："
    items = []
    for r in records[:10]:
        loc = r.get("road_name") or r.get("intersection_id") or r.get("road_segment") or "未知路段"
        cat = f"（{r['category']}）" if r.get("category") else ""
        body = r.get("summary") or r.get("text") or ""
        items.append(f"- {r.get('event_ts','?')} {loc}{cat}：{body}")
    more = f"\n…另有 {n - 10} 条未列出。" if n > 10 else ""
    return head + "\n" + "\n".join(items) + more


def no_hits_answer(question: str) -> str:
    return "未检索到与该问题相关的视频文本事件。可调整时间范围、路段或关键词后再试。"


class VideoQAService:
    """检索 + 合成问答服务。"""

    def __init__(self, store: VideoTextStore, llm_config: LLMConfig | None = None) -> None:
        self.store = store
        self.llm_config = llm_config or LLMConfig.from_env()

    def answer(
        self,
        question: str,
        *,
        base_filters: SearchFilters | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        filters = extract_filters(question, base_filters or SearchFilters())
        if limit is not None:
            filters.limit = limit
        hits = self.store.search(filters)
        evidence = [_evidence_item(h) for h in hits]
        tool_calls = [
            {
                "tool": "search_video_text_events",
                "arguments": _filters_to_args(filters),
                "result": {"count": len(hits)},
            }
        ]
        warnings: list[str] = []

        if not hits:
            warnings.append("未召回相关视频文本事件")
            return {
                "answer": no_hits_answer(question),
                "tool_calls": tool_calls,
                "evidence": evidence,
                "warnings": warnings,
            }

        if self.llm_config.enabled:
            try:
                answer = self._llm_answer(question, hits)
            except LLMError as exc:
                answer = rule_summary(question, hits)
                warnings.append(f"LLM 调用失败，已回退规则摘要：{exc}")
        else:
            answer = rule_summary(question, hits)
            warnings.append("LLM 未启用，回答为规则摘要")

        return {
            "answer": answer,
            "tool_calls": tool_calls,
            "evidence": evidence,
            "warnings": warnings,
        }

    def _llm_answer(self, question: str, records: list[dict[str, Any]]) -> str:
        context = _format_hits_for_llm(records)
        user = (
            f"用户问题：{question}\n\n"
            f"检索到的视频文本事件（共 {len(records)} 条，时间从近到远）：\n{context}\n\n"
            "请仅依据以上事件回答用户问题，给出结论并引用关键时间/路段/摄像头。"
        )
        return chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            config=self.llm_config,
        )
