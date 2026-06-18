"""系统级智能体常量 —— 与 docs/world-status.md §1/§4 一一对应。

唯一来源：窗口参数、计算系数、拥堵阈值都集中在此。调参先改 docs/world-status.md
再改这里，保持文档与代码一致（AGENTS.md §6.2）。
"""

from __future__ import annotations

#: 系统级智能体 ID（docs/naming.md §4）。
SYSTEM_AGENT_ID = "traffic-system-001"

# --- 窗口参数（world-status.md §1）--------------------------------------- #
#: 滚动窗口长度（秒）。
WINDOW_SIZE_SEC = 10
#: 迟到容忍：窗口在 window_end + GRACE_SEC 后关闭结算。
GRACE_SEC = 2
#: 异常过滤：quality.confidence 低于此阈值的上报丢弃。
MIN_CONFIDENCE = 0.3

# --- 计算系数（world-status.md §4）--------------------------------------- #
#: 车均占用长度（米）：排队长度 = 各方向滞留均值之和 × 此值。
VEH_SPACING_M = 7.0
#: 自由流速度（km/h），用于由速度推导延误。
V_FREE_KMH = 40.0
#: 名义路段长度（米）：v1 由速度推导延误时按此段长算行程时间差。
#: delay ≈ max(0, SEGMENT_LEN_M/v_obs − SEGMENT_LEN_M/v_free)（见 world-status.md §4）。
SEGMENT_LEN_M = 200.0
#: 由速度推导延误时，观测速度的下限（m/s），防止极低速导致延误发散。
MIN_SPEED_MPS = 0.5
#: 推导延误的上限（秒），与拥堵档位量级匹配，避免离群值。
MAX_DERIVED_DELAY_SEC = 120.0

# --- 拥堵等级阈值（world-status.md §4，按 mean_delay_sec，单位秒）---------- #
DELAY_SMOOTH_MAX = 27.0  # ≤27 畅通
DELAY_SLOW_MAX = 38.0  # ≤38 缓行
DELAY_CONGESTED_MAX = 52.0  # ≤52 拥堵；>52 严重
#: congestion_index = clamp(mean_delay_sec / 此值, 0, 1)。
CONGESTION_INDEX_DENOM = 60.0

#: 单位换算。
MPS_TO_KMH = 3.6
SECONDS_PER_HOUR = 3600
