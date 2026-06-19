"""检索过滤条件与（规则）问题解析（P7 一阶段）。

一阶段检索 = 关键词 + 时空过滤（向量语义留后续）。本模块是纯逻辑、可单测：
- :class:`SearchFilters`：传给 store.search 的结构化过滤条件；
- :func:`extract_filters`：从自然语言问题里**启发式**补全路段/类别关键词（无 LLM 也能用）。

真正智能的抽取在 LLM 路径里做；这里是规则兜底，且前端通常已带结构化时间/路段过滤。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

#: 常见交通事件类别/关键词词典（命中即作为关键词，部分作为 category）。
CATEGORY_LEXICON: tuple[str, ...] = (
    "事故", "追尾", "碰撞", "刮擦", "拥堵", "缓行", "排队", "违章", "闯红灯",
    "逆行", "压线", "施工", "抛锚", "故障", "积水", "事件", "占道", "行人",
)
#: 作为 category 字段优先匹配的大类。
PRIMARY_CATEGORIES: tuple[str, ...] = ("事故", "拥堵", "违章", "施工")

#: 路名后缀（用于从自由文本里抠出路段）。
_ROAD_SUFFIXES = ("大道", "立交", "隧道", "高架", "环路", "路口", "路", "街", "桥", "道")
_ROAD_RE = re.compile(
    r"[一-龥A-Za-z0-9]{1,8}?(?:" + "|".join(_ROAD_SUFFIXES) + r")"
)
#: 时间/口语短语（抠路名前先剥掉，避免把「下午民族大道」连读错）。
_TIME_PHRASE_RE = re.compile(
    r"\d+\s*月\s*\d+\s*[号日]?|\d{1,2}\s*[:：]\s*\d{2}|\d+\s*点(?:半|\d+分?)?|"
    r"上午|下午|中午|早上|晚上|凌晨|今天|昨天|明天|今早|今晚|本周|周[一二三四五六日天]"
)


@dataclass
class SearchFilters:
    """视频文本检索过滤条件。"""

    time_from: str | None = None  # ISO8601
    time_to: str | None = None
    road_name: str | None = None
    intersection_id: str | None = None
    camera_id: str | None = None
    category: str | None = None
    keywords: list[str] = field(default_factory=list)
    limit: int = 20


def extract_keywords(question: str) -> list[str]:
    """从问题里抽取事件类关键词（按词典命中，保序去重）。"""

    found: list[str] = []
    for kw in CATEGORY_LEXICON:
        if kw in question and kw not in found:
            found.append(kw)
    return found


def extract_category(question: str) -> str | None:
    """命中主类别（事故/拥堵/违章/施工）则作为 category 精过滤。"""

    for cat in PRIMARY_CATEGORIES:
        if cat in question:
            return cat
    return None


def extract_road(question: str) -> str | None:
    """启发式抠路名：先剥时间短语，再按路名后缀匹配最后一个候选。"""

    cleaned = _TIME_PHRASE_RE.sub(" ", question)
    matches = _ROAD_RE.findall(cleaned)
    # 取最长的候选，倾向「民族大道」而非「大道」。
    matches = [m for m in matches if len(m) >= 2]
    if not matches:
        return None
    return max(matches, key=len)


def extract_filters(question: str, base: SearchFilters | None = None) -> SearchFilters:
    """合并显式过滤（base，通常来自前端）与从问题里启发式补全的条件。

    显式过滤优先；缺失项才用启发式补。
    """

    base = base or SearchFilters()
    road = base.road_name or extract_road(question)
    # 关键词策略（兼顾召回与精度）：
    # - 显式 keywords 始终生效；
    # - 否则仅在「无空间过滤（无路段/路口）」时才用问题里抽取的类别词收窄，避免把
    #   宽问题（如「哪里有事故」）拉回全量；
    # - 有路段/路口时不自动加关键词硬过滤——召回靠路段+时间，相关性交给 LLM 归纳，
    #   防止「雄楚大道有什么交通事件」因字面没有「事件」而漏掉该路段全部记录。
    if base.keywords:
        keywords = list(base.keywords)
    elif not (road or base.intersection_id):
        keywords = extract_keywords(question)
    else:
        keywords = []
    # category 仅前端显式指定时作硬过滤。
    return replace(base, road_name=road, keywords=keywords)
