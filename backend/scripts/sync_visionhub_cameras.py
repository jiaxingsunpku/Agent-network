#!/usr/bin/env python3
"""同步 wangxuan visionhub 摄像头/路口目录 → ANP `video_cameras`（轻数据对齐 step1）。

只读真身 `cameras` 表轻字段（无视频/轨迹），ssh+psql 拉取 → 映射 → 全量替换写入 ANP 目录表。
之后 `/api/agent-network/video-text/locations`（位置选择器数据源）即与 wangxuan 一一对应。

配置（`backend/.env`，已被 .gitignore 排除，不入库）：
- ``VISIONHUB_PG_DSN``  必填，如 ``postgresql://tv_user:***@127.0.0.1:5432/trafficvision``
- ``VISIONHUB_SSH_HOST`` 默认 ``wangxuan``

用法：
    python backend/scripts/sync_visionhub_cameras.py            # 同步写入
    python backend/scripts/sync_visionhub_cameras.py --dry-run  # 只拉取+汇总，不写库
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from anp.adapters.visionhub.catalog import fetch_visionhub_cameras, map_camera
from anp.contracts import now_iso
from anp.video.config import _load_dotenv, get_video_config
from anp.video.store import SqliteVideoTextStore


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 wangxuan visionhub 摄像头/路口目录 → ANP video_cameras。")
    ap.add_argument("--ssh-host", default=None, help="覆盖 VISIONHUB_SSH_HOST（默认 wangxuan）")
    ap.add_argument("--dsn", default=None, help="覆盖 VISIONHUB_PG_DSN")
    ap.add_argument("--db", default=None, help="ANP 文本库路径（默认 VideoConfig.db_path）")
    ap.add_argument("--dry-run", action="store_true", help="只拉取+汇总，不写 ANP 库")
    args = ap.parse_args()

    _load_dotenv()
    ssh_host = args.ssh_host or os.getenv("VISIONHUB_SSH_HOST", "wangxuan")
    dsn = args.dsn or os.getenv("VISIONHUB_PG_DSN")
    if not dsn:
        print("ERROR: 需 VISIONHUB_PG_DSN（backend/.env 或 --dsn）", file=sys.stderr)
        return 2

    print(f"[sync-cameras] ssh={ssh_host} 拉取真身 cameras 目录 ...")
    rows = fetch_visionhub_cameras(ssh_host, dsn)
    synced_at = now_iso()
    records = [map_camera(r, synced_at) for r in rows]

    by_inter = Counter(r["intersection_name"] or "(未分类)" for r in records)
    orphans = sum(1 for r in records if not r["intersection_id"])
    print(f"[sync-cameras] 拉取 {len(rows)} 条 source；路口 {len([k for k in by_inter if k != '(未分类)'])} 个；孤儿(无路口) {orphans} 条")
    for name, cnt in by_inter.most_common():
        print(f"    {name}: {cnt}")

    if args.dry_run:
        print("[sync-cameras] --dry-run：不写库。")
        return 0

    cfg = get_video_config()
    store = SqliteVideoTextStore(args.db or cfg.db_path)
    n = store.replace_cameras(records)
    print(f"[sync-cameras] 已全量写入 ANP video_cameras：{n} 条（store.camera_count={store.camera_count()}）")
    print("[sync-cameras] DONE。重启网关后 /locations 即与 wangxuan 一一对应。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
