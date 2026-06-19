#!/usr/bin/env python3
"""运行视频文本 ingest 消费者：订阅 ``anp.video.perception.text.v1`` → 写集中文本库。

live；Ctrl-C 停止。与网关/``run_video_qa.py`` 共用同一 SQLite 文本库（默认
``backend/.data/video_text.db``）。

前置：Kafka 已起、topic 已建。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_video_ingest.py
    run_video_ingest.py --from-beginning      # 从头消费（首次导入历史事件）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anp.video.config import get_video_config  # noqa: E402
from anp.video.ingest import VideoTextIngestConsumer  # noqa: E402
from anp.video.store import SqliteVideoTextStore  # noqa: E402


def main() -> int:
    cfg = get_video_config()
    ap = argparse.ArgumentParser(description="视频文本 ingest（Kafka → 文本库）")
    ap.add_argument("--bootstrap", default=None, help="缺省取 ANP_BOOTSTRAP/localhost:9092")
    ap.add_argument("--db", default=str(cfg.db_path), help="SQLite 文本库路径")
    ap.add_argument("--from-beginning", action="store_true", help="从最早 offset 消费")
    args = ap.parse_args()

    store = SqliteVideoTextStore(args.db)
    consumer = VideoTextIngestConsumer(
        store,
        bootstrap_servers=args.bootstrap,
        auto_offset_reset="earliest" if args.from_beginning else "latest",
    )
    print(f"[ingest] 订阅 anp.video.perception.text.v1 → {args.db}（库内现有 {store.count()} 条）")
    try:
        consumer.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        print(
            f"[ingest] 收尾：ingested={consumer.ingested} dup={consumer.duplicate} "
            f"skipped={consumer.skipped} total={store.count()}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
