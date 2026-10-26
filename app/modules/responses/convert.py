"""OpenAI Responses API 兼容层（Codex CLI 0.1xx 起只认这个协议）。

只做协议转换：/v1/responses ⇄ /v1/chat/completions。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

TOOL_TYPES = {"function"}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in ("input_text", "output_text", "text"):
                    parts.append(str(part.get("text") or ""))
                elif part.get("type") == "refusal":
                    parts.append(str(part.get("refusal") or ""))
        return "\n".join(p for p in parts if p)
    return ""


def to_openai_request(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []

    instructions = body.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": _text_of(instructions)})

    raw_input = body.get("input")
    items = raw_input if isinstance(raw_input, list) else [{"type": "message", "role": "user", "content": raw_input}]

    for item in items:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue

        kind = item.get("type") or ("message" if item.get("role") else "")
        if kind == "message":
            role = str(item.get("role") or "user")
            if role == "developer":
                role = "system"
            text = _text_of(item.get("content"))
            if text:
                messages.append({"role": role, "content": text})
        elif kind == "function_call":
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": item.get("name") or "",
                                "arguments": item.get("arguments")
                                if isinstance(item.get("arguments"), str)
                                else json.dumps(item.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
        elif kind == "function_call_output":
            output = item.get("output")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or "",
                    "content": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                }
            )

    request: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_output_tokens"):
        request["max_tokens"] = body["max_output_tokens"]
    if body.get("temperature") is not None:
        request["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        request["top_p"] = body["top_p"]

    tools = [tool for tool in (body.get("tools") or []) if isinstance(tool, dict) and tool.get("type") in TOOL_TYPES]
    if tools:
        request["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.get("name"),
                    "description": tool.get("description") or "",
                    "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for tool in tools
        ]
        choice = body.get("tool_choice")
        if choice in ("auto", "none", "required"):
            request["tool_choice"] = choice
        elif isinstance(choice, dict) and choice.get("name"):
            request["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
        else:
            request["tool_choice"] = "auto"

    return request


def _usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    return {
        "input_tokens": prompt,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": completion,
        "output_tokens_details": {
            "reasoning_tokens": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        },
        "total_tokens": prompt + completion,
    }


def to_response_object(payload: dict[str, Any], model: str, response_id: str) -> dict[str, Any]:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output: list[dict[str, Any]] = []

    reasoning = message.get("reasoning_content")
    if reasoning:
        output.append(
            {
                "type": "reasoning",
                "id": f"rs_{uuid.uuid4().hex[:16]}",
                "summary": [{"type": "summary_text", "text": str(reasoning)[:2000]}],
            }
        )

    text = message.get("content")
    text = text if isinstance(text, str) else _text_of(text)
    if text:
        output.append(
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex[:16]}",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        output.append(
            {
                "type": "function_call",
                "id": f"fc_{uuid.uuid4().hex[:16]}",
                "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": function.get("name") or "",
                "arguments": function.get("arguments") or "{}",
                "status": "completed",
            }
        )

    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": model,
        "output": output,
        "output_text": text or "",
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": _usage(payload),
        "error": None,
        "incomplete_details": None,
    }


class ResponsesStreamWriter:
    """OpenAI chat chunk → Responses API 事件序列（Codex CLI 依赖这套事件名）。"""

    def __init__(self, model: str, response_id: str):
        self.model = model
        self.response_id = response_id
        self.seq = 0
        self.text_item_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.text_started = False
        self.text_buffer = ""
        self.usage: dict[str, Any] = {}
        self.tools: dict[int, dict[str, Any]] = {}
        self.finish_reason = "stop"

    def _event(self, name: str, data: dict[str, Any]) -> bytes:
        self.seq += 1
        payload = {"type": name, "sequence_number": self.seq, **data}
        return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    def start(self) -> bytes:
        return self._event(
            "response.created",
            {
                "response": {
                    "id": self.response_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "in_progress",
                    "model": self.model,
                    "output": [],
                }
            },
        )

    def _start_text(self, output_index: int) -> bytes:
        self.text_started = True
        return self._event(
            "response.output_item.added",
            {
                "output_index": output_index,
                "item": {
                    "type": "message",
                    "id": self.text_item_id,
                    "status": "in_progress",
                    "role": "assistant",
                    "content": [],
                },
            },
        )

    def feed(self, chunk: dict[str, Any]) -> bytes:
        out = b""
        if chunk.get("usage"):
            self.usage = chunk["usage"]

        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]

            text = delta.get("content")
            if text:
                if not self.text_started:
                    out += self._start_text(len(self.tools))
                self.text_buffer += text
                out += self._event(
                    "response.output_text.delta",
                    {
                        "item_id": self.text_item_id,
                        "output_index": len(self.tools),
                        "content_index": 0,
                        "delta": text,
                    },
                )

            for call in delta.get("tool_calls") or []:
                index = int(call.get("index") or 0)
                state = self.tools.get(index)
                if state is None:
                    state = {
                        "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        "id": f"fc_{uuid.uuid4().hex[:16]}",
                        "name": "",
                        "arguments": "",
                        "added": False,
                        "output_index": (1 if self.text_started else 0) + len(self.tools),
                    }
                    self.tools[index] = state
                function = call.get("function") or {}
                if function.get("name"):
                    state["name"] = function["name"]
                if not state["added"] and state["name"]:
                    state["added"] = True
                    out += self._event(
                        "response.output_item.added",
                        {
                            "output_index": state["output_index"],
                            "item": {
                                "type": "function_call",
                                "id": state["id"],
                                "call_id": state["call_id"],
                                "name": state["name"],
                                "arguments": "",
                                "status": "in_progress",
                            },
                        },
                    )
                arguments = function.get("arguments")
                if arguments:
                    state["arguments"] += arguments
                    out += self._event(
                        "response.function_call_arguments.delta",
                        {
                            "item_id": state["id"],
                            "output_index": state["output_index"],
                            "delta": arguments,
                        },
                    )
        return out

    def finish(self) -> bytes:
        out = b""
        output: list[dict[str, Any]] = []

        if self.text_started:
            item = {
                "type": "message",
                "id": self.text_item_id,
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.text_buffer, "annotations": []}],
            }
            output.append(item)
            out += self._event(
                "response.output_text.done",
                {"item_id": self.text_item_id, "output_index": 0, "content_index": 0, "text": self.text_buffer},
            )
            out += self._event("response.output_item.done", {"output_index": 0, "item": item})

        for state in self.tools.values():
            if not state["added"]:
                continue
            item = {
                "type": "function_call",
                "id": state["id"],
                "call_id": state["call_id"],
                "name": state["name"],
                "arguments": state["arguments"] or "{}",
                "status": "completed",
            }
            output.append(item)
            out += self._event(
                "response.function_call_arguments.done",
                {
                    "item_id": state["id"],
                    "output_index": state["output_index"],
                    "arguments": item["arguments"],
                },
            )
            out += self._event("response.output_item.done", {"output_index": state["output_index"], "item": item})

        out += self._event(
            "response.completed",
            {
                "response": {
                    "id": self.response_id,
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "completed",
                    "model": self.model,
                    "output": output,
                    "output_text": self.text_buffer,
                    "usage": _usage({"usage": self.usage}),
                }
            },
        )
        return out


def error_body(message: str, kind: str = "server_error") -> dict[str, Any]:
    return {"error": {"message": message, "type": kind, "param": None, "code": None}}


__all__ = [
    "ResponsesStreamWriter",
    "error_body",
    "to_openai_request",
    "to_response_object",
]
