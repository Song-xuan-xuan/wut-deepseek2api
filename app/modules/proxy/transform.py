"""请求/响应整形。

原则：白名单式修正 —— 只动已知有毒的字段，其余原样透传。
（错误体格式在 modules/shared.py，这里只管参数和模型列表。）
"""

from __future__ import annotations

import re
from typing import Any

from ...core.settings import Settings

_MAX_TOKEN_FIELDS = ("max_completion_tokens", "max_output_tokens")


def resolve_model_alias(body: dict[str, Any], aliases: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """把客户端用的别名换成上游真实模型 id。

    用途：同一模型想配两套上下文（例如「日常 200K」和「长文 700K」），
    客户端看到的 id 不同，但上游收到的都是 deepseek-v4-flash。
    """
    name = str(body.get("model") or "")
    target = aliases.get(name)
    if not target or target == name:
        return body, []
    return {**body, "model": target}, [f"alias {name}->{target}"]


def sanitize_chat_request(body: dict[str, Any], settings: Settings) -> tuple[dict[str, Any], list[str]]:
    out = dict(body)
    notes: list[str] = []

    # 1. 统一 max_tokens（OpenWebUI / DeepSeek 只认这个）
    for field in _MAX_TOKEN_FIELDS:
        if field in out:
            if out.get("max_tokens") is None:
                out["max_tokens"] = out.pop(field)
                notes.append(f"{field}->max_tokens")
            else:
                out.pop(field, None)

    # 2. 钳制输出上限
    max_tokens = out.get("max_tokens")
    if settings.max_tokens_cap and isinstance(max_tokens, int) and max_tokens > settings.max_tokens_cap:
        notes.append(f"max_tokens {max_tokens}->{settings.max_tokens_cap}")
        out["max_tokens"] = settings.max_tokens_cap

    # 3. 剔除上游不一定认识的字段
    for field in settings.strip_fields:
        if field in out:
            out.pop(field)
            notes.append(f"drop {field}")

    # 4. 需要时打开原生工具调用（仅 Ollama 连接有意义）
    if settings.enable_native_tool_calling and out.get("tools"):
        params = dict(out.get("params") or {})
        params["function_calling"] = "native"
        out["params"] = params
        notes.append("function_calling=native")

    if not out.get("model") and settings.default_model:
        out["model"] = settings.default_model

    return out, notes


def _pick_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def to_openai_model_list(payload: Any, extra: list[Any] | None = None) -> dict[str, Any]:
    """兼容 OpenWebUI 的 {data:[...]} / 裸数组，以及 Ollama 的 {models:[...]}。"""
    seen: set[str] = set()
    data: list[dict[str, Any]] = []

    def push(item: Any) -> None:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            return
        model_id = item.get("id") or item.get("model") or item.get("name")
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        data.append(
            {
                "id": model_id,
                "object": "model",
                "created": int(item.get("created") or 0) or 1_700_000_000,
                "owned_by": item.get("owned_by") or "whut",
                "name": item.get("name") or model_id,
            }
        )

    for item in _pick_items(payload):
        push(item)
    for item in extra or []:
        push(item)
    return {"object": "list", "data": data}


def upstream_error_message(status: int, data: Any, text: str) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            parts = []
            for item in detail:
                parts.append(str(item.get("msg") if isinstance(item, dict) else item))
            return "; ".join(parts)
    raw = (text or "").strip()
    if raw and not raw.startswith("<"):
        return raw[:500]
    return f"上游返回 {status}"


def ascii_notes(notes: list[str]) -> str:
    """HTTP 头只能是 latin1，notes 里的中文/箭头要剥掉。"""
    joined = ",".join(notes)
    return re.sub(r"[^\x20-\x7E]", "", joined).replace(",,", ",")


def normalize_upstream_status(status: int, message: str) -> int:
    """学校把限流包成 400 返回（消息里写着 429），还原成 429 让客户端正确退避。"""
    if status == 400 and message.strip().startswith("429"):
        return 429
    return status


# 学校把上游的临时故障也塞进 400/403 里，这类错误重试通常就好了
_TRANSIENT_PATTERNS = (
    "server connection error",
    "connection error",
    "open webui:",
    "upstream",
    "timeout",
    "timed out",
    "overloaded",
    "temporarily",
    "try again",
    "bad gateway",
    "service unavailable",
    "internal error",
)


def looks_transient(message: str) -> bool:
    """粗判一条上游错误是不是“重试可能成功”的临时故障。"""
    text = (message or "").lower()
    return any(pattern in text for pattern in _TRANSIENT_PATTERNS)
