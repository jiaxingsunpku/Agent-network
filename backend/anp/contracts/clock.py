"""世界时钟 v1 —— ANP 多源感知的统一时间基与新鲜度工具（docs/world-clock.md）。

统一世界时基 = **UTC 挂钟**。任何感知源（SUMO 适配器、视频组摄像头、未来真实设备）以
``envelope.time.event_ts`` = **权威事件时刻**（观测对应的真实世界时刻，**非发送时刻**）接入；
该 event_ts 经各层透传贯穿全链（status 透传观测的 event_ts；control 带 ``based_on_event_ts``），
交通智能体 / 执行端据此算新鲜度、判过期：``age = now − origin_event_ts``。

轻版 v1：只做**时间契约 + 新鲜度工具**，不做应用层跨机时钟偏移校正——跨机部署假设各机
NTP 同步（docs/world-clock.md「部署要求」）。``sim_clock``（仿真时钟）保留作 SUMO 源的辅助
元数据，**不再作跨源主判据**（取代只对 SUMO 有意义的 ``sim_step`` 过期判据）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from .envelope import parse_iso

#: 默认观测新鲜度上限（秒）。视频组每 5s 发一次 → 容忍 ~2 个间隔的抖动；各执行端可按源覆盖。
DEFAULT_MAX_AGE_SEC = 12.0


def now_utc() -> datetime:
    """当前 UTC 时间（带 tz）。统一世界时基的取值口。"""

    return datetime.now(timezone.utc)


def age_seconds(origin_event_ts: str, now: datetime | None = None) -> float:
    """``origin_event_ts``（ISO8601 UTC）到现在的挂钟秒数。

    可为负（event_ts 在未来，时钟漂移/抢跑时）；调用方按需裁剪。
    """

    ref = now or now_utc()
    return (ref - parse_iso(origin_event_ts)).total_seconds()


def is_fresh(origin_event_ts: str | None, max_age_sec: float = DEFAULT_MAX_AGE_SEC,
             now: datetime | None = None) -> bool:
    """观测是否新鲜（``0 ≤ age ≤ max_age_sec``）。

    ``origin_event_ts`` 缺失或解析失败 → 按**不新鲜**处理（保守，让执行端回落兜底）。
    未来时间（age<0，超过 1s 容差）也视为不可信、不新鲜。
    """

    if not origin_event_ts:
        return False
    try:
        age = age_seconds(origin_event_ts, now)
    except Exception:  # noqa: BLE001 - 非法时间串保守判不新鲜
        return False
    return -1.0 <= age <= max_age_sec
