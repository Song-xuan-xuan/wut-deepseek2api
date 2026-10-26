"""Anthropic Messages 兼容层（让 Claude Code 这类客户端直接用上）。

只做协议转换：Anthropic /v1/messages ⇄ OpenAI /v1/chat/completions。
真正的上游调用仍然走 proxy 模块，所以参数钳制、重试、熔断、用量统计全部复用。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

# ---------------------------------------------------------------- 请求转换


def _blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def to_openai_request(body: dict[str, Any], default_max_tokens: int = 4096) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []

    system = body.get("system")
    if system:
        messages.append({"role": "system", "content": _blocks_to_text(system)})

    for message in body.get("messages") or []:
        role = message.get("role")
        content = message.get("content")

        if role == "assistant":
            texts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": block.get("name") or "",
                                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                            },
                        }
                    )
            item: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            messages.append(item)
            continue

        if role == "user":
            # user 消息里可能混着 tool_result，要拆成独立的 role:tool 消息
            if isinstance(content, list):
                plain = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id") or "",
                                "content": _tool_result_text(block.get("content")),
                            }
                        )
                    elif isinstance(block, dict) and block.get("type") == "text":
                        plain.append(str(block.get("text") or ""))
                    elif isinstance(block, str):
                        plain.append(block)
                text = "\n".join(plain)
            else:
                text = _blocks_to_text(content)
            if text:
                messages.append({"role": "user", "content": text})
            continue

        messages.append({"role": str(role), "content": _blocks_to_text(content)})

    request: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "max_tokens": body.get("max_tokens") or default_max_tokens,
        "stream": bool(body.get("stream")),
    }
    if body.get("temperature") is not None:
        request["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        request["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        request["stop"] = body["stop_sequences"]

    tools = body.get("tools") or []
    if tools:
        request["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        ]
        choice = body.get("tool_choice") or {}
        kind = choice.get("type") if isinstance(choice, dict) else None
        if kind == "any":
            request["tool_choice"] = "required"
        elif kind == "tool":
            request["tool_choice"] = {"type": "function", "function": {"name": choice.get("name")}}
        elif kind == "none":
            request["tool_choice"] = "none"
        else:
            request["tool_choice"] = "auto"

    return request


# ---------------------------------------------------------------- 响应转换

STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def to_anthropic_response(payload: dict[str, Any], requested_model: str) -> dict[str, Any]:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []

    reasoning = message.get("reasoning_content")
    if reasoning:
        content.append({"type": "thinking", "thinking": reasoning, "signature": ""})

    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    elif isinstance(text, list):
        joined = "".join(str(part.get("text") or "") for part in text if isinstance(part, dict))
        if joined:
            content.append({"type": "text", "text": joined})

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            parsed = {"_raw": raw}
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": function.get("name") or "",
                "input": parsed,
            }
        )

    usage = payload.get("usage") or {}
    return {
        "id": f"msg_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": requested_model or payload.get("model") or "",
        "content": content or [{"type": "text", "text": ""}],
        "stop_reason": STOP_REASON_MAP.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


class AnthropicStreamWriter:
    """把 OpenAI 的 SSE 增量翻译成 Anthropic 的 SSE 事件序列。

    事件顺序（Claude Code 依赖这个顺序）：
        message_start → (content_block_start → deltas → content_block_stop)* → message_delta → message_stop
    """

    def __init__(self, model: str):
        self.model = model
        self.started = False
        self.block_index = -1
        self.text_open = False
        self.thinking_open = False
        self.tools: dict[int, dict[str, Any]] = {}
        self.stop_reason = "end_turn"
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    # ------------------------------------------------------------ 事件

    def _event(self, name: str, data: dict[str, Any]) -> bytes:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    def _start(self) -> bytes:
        self.started = True
        return self._event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_{uuid.uuid4().hex[:20]}",
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

    def _close_text(self) -> bytes:
        if not self.text_open:
            return b""
        self.text_open = False
        return self._event("content_block_stop", {"type": "content_block_stop", "index": self.block_index})

    def _close_thinking(self) -> bytes:
        if not self.thinking_open:
            return b""
        self.thinking_open = False
        return self._event("content_block_stop", {"type": "content_block_stop", "index": self.block_index})

    def _open_text(self) -> bytes:
        if self.text_open:
            return b""
        out = self._close_thinking()
        self.block_index += 1
        self.text_open = True
        out += self._event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self.block_index,
                "content_block": {"type": "text", "text": ""},
            },
        )
        return out

    def _open_thinking(self) -> bytes:
        if self.thinking_open:
            return b""
        self.block_index += 1
        self.thinking_open = True
        return self._event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self.block_index,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        )

    # ------------------------------------------------------------ 主入口

    def feed(self, chunk: dict[str, Any]) -> bytes:
        out = b""
        if not self.started:
            out += self._start()

        if chunk.get("usage"):
            usage = chunk["usage"]
            self.usage = {
                "input_tokens": int(usage.get("prompt_tokens") or self.usage["input_tokens"]),
                "output_tokens": int(usage.get("completion_tokens") or self.usage["output_tokens"]),
            }

        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            finish = choice.get("finish_reason")
            if finish:
                self.stop_reason = STOP_REASON_MAP.get(finish, "end_turn")

            reasoning = delta.get("reasoning_content")
            if reasoning:
                out += self._open_thinking()
                out += self._event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "thinking_delta", "thinking": reasoning},
                    },
                )

            text = delta.get("content")
            if text:
                out += self._open_text()
                out += self._event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "text_delta", "text": text},
                    },
                )

            for call in delta.get("tool_calls") or []:
                index = int(call.get("index") or 0)
                state = self.tools.get(index)
                if state is None:
                    out += self._close_text()
                    out += self._close_thinking()
                    self.block_index += 1
                    state = {
                        "block": self.block_index,
                        "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                        "name": (call.get("function") or {}).get("name") or "",
                        "started": False,
                    }
                    self.tools[index] = state
                function = call.get("function") or {}
                if function.get("name") and not state["name"]:
                    state["name"] = function["name"]
                if not state["started"] and state["name"]:
                    state["started"] = True
                    out += self._event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": state["block"],
                            "content_block": {
                                "type": "tool_use",
                                "id": state["id"],
                                "name": state["name"],
                                "input": {},
                            },
                        },
                    )
                arguments = function.get("arguments")
                if arguments and state["started"]:
                    out += self._event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state["block"],
                            "delta": {"type": "input_json_delta", "partial_json": arguments},
                        },
                    )
        return out

    def finish(self) -> bytes:
        out = b""
        if not self.started:
            out += self._start()
        out += self._close_text()
        out += self._close_thinking()
        for state in self.tools.values():
            out += self._event("content_block_stop", {"type": "content_block_stop", "index": state["block"]})
        out += self._event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                "usage": self.usage,
            },
        )
        out += self._event("message_stop", {"type": "message_stop"})
        return out


def estimate_tokens(body: dict[str, Any]) -> int:
    """count_tokens 的粗估：Claude Code 只用它做上下文预警，精度要求不高。"""
    text = json.dumps(body, ensure_ascii=False)
    return max(1, len(text) // 3)


def error_body(message: str, kind: str = "api_error") -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


__all__ = [
    "AnthropicStreamWriter",
    "error_body",
    "estimate_tokens",
    "time",
    "to_anthropic_response",
    "to_openai_request",
]
