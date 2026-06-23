"""视频域配置（P7）。

集中读取：SQLite 文本库路径、视频域智能体 id、LLM（GLM / z.ai OpenAI 兼容）参数。
LLM 参数从环境变量读取（``backend/.env``，已被 .gitignore 排除）；本模块在导入时尝试
加载该 .env（不覆盖已存在的环境变量），保证脚本/服务无需手动 source 也能拿到 key。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: 仓库根（backend/anp/video/config.py → parents[3] = repo root）。
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "backend" / ".data" / "video_text.db"
#: 协作视频任务存储（P9）；与文本库同目录、独立文件。
DEFAULT_TASK_DB_PATH = REPO_ROOT / "backend" / ".data" / "video_tasks.db"
DOTENV_PATH = REPO_ROOT / "backend" / ".env"

#: 视频域智能体身份（docs/naming.md §4）。
VIDEO_PERCEPTION_AGENT_ID = "video-perception-001"  # 视频感知体（发文本事件）
VIDEO_TASK_AGENT_ID = "video-task-001"  # 问答任务体（检索 + 合成）

#: 本机 clash 默认代理（CLAUDE.md 全局指令）；z.ai 国际站需经代理。
_DEFAULT_PROXY = "http://127.0.0.1:7897"


def _load_dotenv(path: Path = DOTENV_PATH) -> None:
    """极简 .env 加载（零依赖）：仅在变量未设置时填入，不覆盖已有环境变量。"""

    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass(frozen=True)
class LLMConfig:
    """问答合成的 LLM（OpenAI 兼容；默认 GLM via z.ai）。"""

    base_url: str | None
    model: str
    api_key: str | None
    proxy: str | None
    timeout_s: float = 60.0
    # glm-5.2 为推理模型：reasoning_content 与 answer 共用 max_tokens，给足以免 content 被吃空。
    max_tokens: int = 4096
    temperature: float = 0.2

    @property
    def enabled(self) -> bool:
        """有 base_url + key 才启用真 LLM；否则 QA 走规则摘要兜底。"""

        return bool(self.base_url and self.api_key)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        _load_dotenv()
        base_url = os.getenv("OPENAI_BASE_URL")
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "glm-5.2")
        # 代理：ANP_LLM_PROXY 显式覆盖（设为空串可禁用）；否则用 HTTPS_PROXY/ALL_PROXY；
        # 都没有则回退本机 clash。仅用于 LLM 出网调用，本地 Kafka/网关不受影响。
        if "ANP_LLM_PROXY" in os.environ:
            proxy = os.environ["ANP_LLM_PROXY"] or None
        else:
            proxy = (
                os.getenv("HTTPS_PROXY")
                or os.getenv("https_proxy")
                or os.getenv("ALL_PROXY")
                or os.getenv("all_proxy")
                or _DEFAULT_PROXY
            )
        return cls(base_url=base_url, model=model, api_key=api_key, proxy=proxy)


@dataclass(frozen=True)
class VideoConfig:
    """视频域运行配置。"""

    db_path: Path = DEFAULT_DB_PATH
    task_db_path: Path = DEFAULT_TASK_DB_PATH
    perception_agent_id: str = VIDEO_PERCEPTION_AGENT_ID
    task_agent_id: str = VIDEO_TASK_AGENT_ID
    default_query_limit: int = 20
    max_query_limit: int = 100

    @staticmethod
    def llm() -> LLMConfig:
        return LLMConfig.from_env()


_CONFIG: VideoConfig | None = None


def get_video_config() -> VideoConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = VideoConfig()
    return _CONFIG
