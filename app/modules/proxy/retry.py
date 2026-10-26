"""上游重试策略：三个协议模块共用。

学校这套 OpenWebUI 的失败模式很杂：
- 上游网关抖动会被包成 `400 Server Connection Error`（甚至 `400 429: ...`）；
- token 过期是 `401/403`，续期后立刻可用；
- 真·参数错误也是 `400`，这种重试没意义。

所以策略是：**401/403 先续期 token 再重试；400/403 只有“看起来像抖动”时才重试**，
其余一路直接返回，不浪费上游时间。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .transform import looks_transient, normalize_upstream_status

RETRYABLE_400_FAMILY = {400, 403, 408, 425}
MAX_BACKOFF = 2.0


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


async def chat_with_retry(
    app: Any,
    body: dict[str, Any],
    *,
    notes: list[str],
    req_log: Any,
    tag: str = "chat",
) -> tuple[Any, int, str]:
    """发一次对话请求，必要时重试。

    返回 `(response, status, message)`：
    - 成功时 status = response.status_code（< 400），message 为空；
    - 重试用尽仍失败时 response 为 None，status/message 是最后一次的错误。
    """
    settings = app.state.settings
    provider = app.state.provider
    max_retries = max(0, int(getattr(settings.upstream, "transient_retries", 0)))
    model = str(body.get("model") or "")

    status = 0
    message = ""
    for attempt in range(max_retries + 1):
        resp = await provider.chat(body)
        if resp.status_code < 400:
            return resp, resp.status_code, ""

        text = (await resp.aread()).decode("utf-8", "replace")
        await resp.aclose()
        message = _message_of(resp.status_code, text)
        status = normalize_upstream_status(resp.status_code, message)

        retried = False
        if status in (401, 403):
            auth = getattr(app.state, "auth", None)
            if auth is not None:
                await auth.refresh(f"{tag} {status}")
            # 续期不一定成功（没配学号 / 学校还没修好），403 也值得再试一次：
            # 见过学校 WAF 偶发返回 403，重试就好
            retried = True
        elif status in RETRYABLE_400_FAMILY and looks_transient(message):
            retried = True

        if not retried or attempt >= max_retries:
            break

        notes.append(f"retry#{attempt + 1}({status})")
        req_log(f"{tag} 重试 {attempt + 1}/{max_retries}（{status} {message[:60]!r}）")
        await asyncio.sleep(min(MAX_BACKOFF, 0.4 * (attempt + 1)))

    app.state.model_guard.record_failure(model, status)
    return None, status, message


def _message_of(status: int, text: str) -> str:
    from .transform import upstream_error_message

    return upstream_error_message(status, _parse(text), text)
