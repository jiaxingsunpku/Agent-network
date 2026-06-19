#!/usr/bin/env python3
"""由 contracts 的 Pydantic 模型导出 JSON Schema 到 ``schemas/``。

契约的唯一来源是 ``backend/anp/contracts`` 的代码；``schemas/*.json`` 是其派生产物，
不要手改。改契约后重跑本脚本，使 schema 与代码保持一致（AGENTS.md §6）。

用法::

    backend/.venv/bin/python backend/scripts/gen_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

from anp.contracts import (
    AckPayload,
    AgentHeartbeatPayload,
    AgentLifecyclePayload,
    CommandPayload,
    Envelope,
    IntersectionStatusPayload,
    ObservationPayload,
    VideoTextEventPayload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "schemas"

#: 文件名 → (模型, schema $id 用的标题)
TARGETS = {
    "envelope.schema.json": (Envelope, "ANP Envelope（统一消息外壳，docs/protocol.md §1）"),
    "observation.schema.json": (ObservationPayload, "交通路口观测 payload（docs/world-status.md §2）"),
    "video_text.schema.json": (VideoTextEventPayload, "视频文本事件 payload（docs/video.md）"),
    "status.intersection.schema.json": (
        IntersectionStatusPayload,
        "路口 World Status payload（docs/world-status.md §3）",
    ),
    "command.schema.json": (CommandPayload, "命令 payload（docs/protocol.md §5）"),
    "ack.schema.json": (AckPayload, "命令回执 payload（docs/protocol.md §5）"),
    "agent.lifecycle.schema.json": (AgentLifecyclePayload, "智能体注册/下线 payload"),
    "agent.heartbeat.schema.json": (AgentHeartbeatPayload, "智能体心跳 payload"),
}


def main() -> int:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (model, title) in TARGETS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = title
        path = SCHEMAS_DIR / filename
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
