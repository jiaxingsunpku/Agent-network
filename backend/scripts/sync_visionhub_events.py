#!/usr/bin/env python3
"""同步 wangxuan visionhub 历史事件 → ANP 文本事件库（轻数据对齐 step3）。

镜像 step1 目录同步（``sync_visionhub_cameras.py``）：ssh+psql 拉真身 ``events`` 表**轻字段**
（``description`` 文本 / ``event_type`` 类别 / ``detected_at`` 时间 / ``severity`` / ``source_id``
关联键 / ``track_ids`` 轻元数据——**绝不含 bbox/帧/轨迹像素**）→ 经已同步的目录把 ``source_id``
映射成 ``camera_id``/``intersection_id``/``road_name`` → 直写 ANP 文本库（一次性历史回填，幂等：
``message_id=vh-evt-<真身id>`` 主键去重；live 回流仍走 Kafka）。

回填后这些事件经 step2 的连接挂到对应天津相机/路口下，``/locations`` 与事件数据库视图即见真实数据。

配置（`backend/.env`，.gitignore 排除，不入库）：
- ``VISIONHUB_PG_DSN``  必填
- ``VISIONHUB_SSH_HOST`` 默认 ``wangxuan``

前置：先跑过 ``sync_visionhub_cameras.py``（需目录把 source_id 对齐到相机/路口）。

用法：
    python backend/scripts/sync_visionhub_events.py                       # 全量同步
    python backend/scripts/sync_visionhub_events.py --limit 50            # 抽样前 50 条
    python backend/scripts/sync_visionhub_events.py --event-type traffic_congestion  # 只拥堵
    python backend/scripts/sync_visionhub_events.py --dry-run             # 只拉取+汇总，不写库
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from anp.adapters.visionhub.catalog import (
    CATEGORY_BY_EVENT_TYPE,
    fetch_visionhub_events,
    map_event,
)
from anp.video.config import _load_dotenv, get_video_config
from anp.video.models import VideoTextEventIn
from anp.video.store import SqliteVideoTextStore


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 wangxuan visionhub 历史事件 → ANP 文本事件库。")
    ap.add_argument("--ssh-host", default=None, help="覆盖 VISIONHUB_SSH_HOST（默认 wangxuan）")
    ap.add_argument("--dsn", default=None, help="覆盖 VISIONHUB_PG_DSN")
    ap.add_argument("--db", default=None, help="ANP 文本库路径（默认 VideoConfig.db_path）")
    ap.add_argument("--limit", type=int, default=None, help="最多同步条数（按 detected_at 升序，抽样用）")
    ap.add_argument("--event-type", action="append", default=None,
                    choices=sorted(CATEGORY_BY_EVENT_TYPE), help="只同步指定事件类型（可多次）")
    ap.add_argument("--dry-run", action="store_true", help="只拉取+汇总，不写 ANP 库")
    args = ap.parse_args()

    _load_dotenv()
    ssh_host = args.ssh_host or os.getenv("VISIONHUB_SSH_HOST", "wangxuan")
    dsn = args.dsn or os.getenv("VISIONHUB_PG_DSN")
    if not dsn:
        print("ERROR: 需 VISIONHUB_PG_DSN（backend/.env 或 --dsn）", file=sys.stderr)
        return 2

    cfg = get_video_config()
    store = SqliteVideoTextStore(args.db or cfg.db_path)
    source_map = store.camera_source_index()
    if not source_map:
        print("WARN: ANP 目录为空——请先跑 sync_visionhub_cameras.py，否则事件无法对齐相机/路口。", file=sys.stderr)

    event_types = tuple(args.event_type) if args.event_type else None
    print(f"[sync-events] ssh={ssh_host} 拉取真身 events 轻字段 "
          f"(limit={args.limit or '全量'}, types={event_types or '全部'}) ...")
    rows = fetch_visionhub_events(ssh_host, dsn, limit=args.limit, event_types=event_types)
    records = [map_event(r, source_map) for r in rows]

    by_cat = Counter(r["category"] or "(未分类)" for r in records)
    unaligned = sum(1 for r in records if not r["intersection_id"] and r["camera_id"].startswith("vh-source-"))
    print(f"[sync-events] 拉取 {len(rows)} 条；类别分布：" + ", ".join(f"{k}={v}" for k, v in by_cat.most_common()))
    print(f"[sync-events] 经目录对齐到相机/路口：{len(records) - unaligned}/{len(records)}"
          f"（未命中目录 source 的 {unaligned} 条用合成 camera_id 入库、不挂目录）")

    if args.dry_run:
        print("[sync-events] --dry-run：不写库。样例 3 条：")
        for r in records[:3]:
            print(f"    [{r['category']}] cam={r['camera_id']} inter={r['intersection_id']} :: {r['text'][:48]}")
        return 0

    new = dup = bad = 0
    for rec in records:
        try:
            env = VideoTextEventIn.model_validate(rec).to_envelope(default_agent_id=rec["source_agent_id"])
        except Exception as exc:  # noqa: BLE001 - 单条映射非法不阻断整体回填
            bad += 1
            if bad <= 5:
                print(f"    跳过非法记录 id={rec.get('message_id')}: {exc}", file=sys.stderr)
            continue
        if store.append(env):
            new += 1
        else:
            dup += 1
    print(f"[sync-events] 写入完成：new={new} dup={dup} bad={bad}（库内事件总数 store.count={store.count()}）")
    print("[sync-events] DONE。重启网关后 /locations 与事件数据库视图即见真实事件挂到天津相机/路口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
