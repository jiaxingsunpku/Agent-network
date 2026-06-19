"""问答合成的 LLM 客户端（P7）—— OpenAI 兼容 chat completions，默认 GLM（z.ai）。

零依赖：用标准库 ``urllib`` 出网。代理仅作用于本次 LLM 调用（``config.proxy``），
不影响本地 Kafka/网关流量。GLM-5.2 为推理模型，答案取 ``choices[0].message.content``
（``reasoning_content`` 不用），故 ``max_tokens`` 需给足。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import LLMConfig


class LLMError(RuntimeError):
    """LLM 不可用或响应异常；上层据此回退规则摘要。"""


def chat(messages: list[dict[str, str]], *, config: LLMConfig) -> str:
    """单轮 chat completion，返回 ``message.content``（已 strip）。失败抛 :class:`LLMError`。"""

    if not config.enabled:
        raise LLMError("LLM 未配置（缺 OPENAI_BASE_URL / OPENAI_API_KEY）")

    url = config.base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
    )
    # 显式 opener：有代理走代理，无代理则空 ProxyHandler（不继承环境代理）。
    proxies = {"http": config.proxy, "https": config.proxy} if config.proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    try:
        with opener.open(req, timeout=config.timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"LLM 请求失败: {exc}") from exc

    try:
        obj: dict[str, Any] = json.loads(raw)
        content = (obj["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM 响应解析失败: {exc}; raw={raw[:300]}") from exc

    if not content:
        raise LLMError("LLM 返回空 content（推理模型 max_tokens 可能不足）")
    return content
