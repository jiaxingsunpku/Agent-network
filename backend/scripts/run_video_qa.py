#!/usr/bin/env python3
"""运行视频文本问答服务（独立 FastAPI/uvicorn）。

通常问答路由已 co-host 在网关进程（``run_gateway.py`` 自动挂载
``/api/agent-network/video-text/*``）。本脚本用于单独起一个视频问答服务（如调试或
与网关分离部署）。读同一 SQLite 文本库（``run_video_ingest.py`` 写入）。

问答合成默认接 GLM（``backend/.env``：``OPENAI_BASE_URL/MODEL/API_KEY``，z.ai 需经代理）；
无 key 时回退规则摘要。

用法::

    /home/sjx/miniconda3/envs/anp/bin/python backend/scripts/run_video_qa.py --port 8030
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from anp.video.config import VideoConfig, get_video_config  # noqa: E402
from anp.video.routes import create_video_app  # noqa: E402


def main() -> int:
    cfg = get_video_config()
    ap = argparse.ArgumentParser(description="视频文本问答服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8030)
    ap.add_argument("--db", default=str(cfg.db_path), help="SQLite 文本库路径")
    args = ap.parse_args()

    app = create_video_app(VideoConfig(db_path=Path(args.db)))
    llm = VideoConfig.llm()
    print(f"[video-qa] LLM enabled={llm.enabled} model={llm.model}（无 key 走规则摘要）")
    print(f"[video-qa] 监听 http://{args.host}:{args.port}/api/agent-network/video-text/{{events,query,health}}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
