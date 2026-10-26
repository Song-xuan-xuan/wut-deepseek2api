"""POST /v1/messages —— 让 Claude Code / Anthropic SDK 直接可用。

实现要点：
- 复用 proxy 模块的 provider（参数钳制、重试、熔断、用量统计全都沿用）；
- 流式把 OpenAI 的 delta 翻译成 Anthropic 事件序列；
- 客户端断开时及时取消上游请求，不留下僵尸连接。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...core.logging import log, noop
from ...core.settings import Settings
from ...providers.base import Provider, ProviderError
from ..proxy.transform import sanitize_chat_request
from ..usage.store import UsageStore
from .convert import (
    AnthropicStreamWriter,
    error_body,
    estimate_tokens,
    to_anthropic_response,
    to_openai_request,
)


def attach(
    app: FastAPI,
    settings: Settings,
    provider: Provider,
    store: UsageStore,
    require_key: Any,
) -> None:
    router = APIRouter()
    req_log = log if settings.log_requests else noop

    @router.post("/v1/messages")
    async def messages(request: Request, _: None = Depends(require_key)):
        started = time.time()
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error_body("请求体不是合法 JSON", "invalid_request_error"), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(error_body("请求体必须是 JSON 对象", "invalid_request_error"), status_code=400)

        key_id = int(getattr(request.state, "key_id", 0) or 0)
        requested_model = str(body.get("model") or "")
        try:
            openai_body, notes = sanitize_chat_request(
                to_openai_request(body, default_max_tokens=settings.max_tokens_cap or 4096), settings
            )
        except Exception as exc:  # noqa: BLE001 - 转换失败要给客户端可读错误
            return JSONResponse(error_body(f"请求转换失败：{exc}", "invalid_request_error"), status_code=400)

        meta = f"anthropic model={openai_body.get('model')} stream={bool(openai_body.get('stream'))}"

        try:
            resp = await provider.chat(openai_body)
            if resp.status_code in (401, 403):
                auth = getattr(app.state, "auth", None)
                if auth is not None and await auth.refresh("Anthropic 401/403"):
                    await resp.aclose()
                    resp = await provider.chat(openai_body)
        except ProviderError as exc:
            store.record(
                model=str(openai_body.get("model") or ""),
                status=exc.status,
                ms=(time.time() - started) * 1000,
                stream=bool(openai_body.get("stream")),
                tools=len(openai_body.get("tools") or []),
                notes=notes,
                error=exc.message,
                key_id=key_id,
            )
            return JSONResponse(error_body(exc.message), status_code=exc.status)

        if resp.status_code >= 400:
            text = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            message = text[:500]
            try:
                data = json.loads(text)
                message = (
                    data.get("detail")
                    or (data.get("error") or {}).get("message")
                    or message
                )
            except (json.JSONDecodeError, AttributeError):
                pass
            store.record(
                model=str(openai_body.get("model") or ""),
                status=resp.status_code,
                ms=(time.time() - started) * 1000,
                stream=bool(openai_body.get("stream")),
                tools=len(openai_body.get("tools") or []),
                notes=notes,
                error=str(message)[:500],
                key_id=key_id,
            )
            req_log(f"POST /v1/messages {resp.status_code} {int((time.time() - started) * 1000)}ms {meta}")
            return JSONResponse(error_body(str(message)), status_code=resp.status_code)

        content_type = resp.headers.get("content-type", "")
        streaming = "event-stream" in content_type or (
            openai_body.get("stream") and "application/json" not in content_type
        )

        if streaming:
            req_log(f"POST /v1/messages 200 {int((time.time() - started) * 1000)}ms {meta} sse")

            async def events() -> AsyncIterator[bytes]:
                writer = AnthropicStreamWriter(requested_model or str(openai_body.get("model") or ""))
                buffer = ""
                saw_finish = False
                try:
                    async for raw in resp.aiter_bytes():
                        buffer += raw.decode("utf-8", "replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                saw_finish = True
                                continue
                            try:
                                chunk = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("error"):
                                message = str((chunk["error"] or {}).get("message") or chunk["error"])
                                yield b"event: error\ndata: " + json.dumps(
                                    error_body(message), ensure_ascii=False
                                ).encode() + b"\n\n"
                                return
                            out = writer.feed(chunk)
                            if out:
                                yield out
                    yield writer.finish()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    yield b"event: error\ndata: " + json.dumps(
                        error_body(f"上游流中断：{exc}"), ensure_ascii=False
                    ).encode() + b"\n\n"
                finally:
                    with contextlib.suppress(Exception):
                        await resp.aclose()
                    store.record(
                        model=str(openai_body.get("model") or ""),
                        status=200 if saw_finish else 206,
                        ms=(time.time() - started) * 1000,
                        stream=True,
                        tools=len(openai_body.get("tools") or []),
                        usage=writer.usage,
                        tool_calls=len(writer.tools),
                        notes=notes,
                        key_id=key_id,
                    )

            return StreamingResponse(
                events(),
                media_type="text/event-stream; charset=utf-8",
                headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
            )

        data = await resp.aread()
        await resp.aclose()
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return JSONResponse(error_body("上游返回了非 JSON 内容"), status_code=502)

        converted = to_anthropic_response(payload, requested_model)
        usage = payload.get("usage") or {}
        store.record(
            model=str(openai_body.get("model") or ""),
            status=200,
            ms=(time.time() - started) * 1000,
            stream=False,
            tools=len(openai_body.get("tools") or []),
            usage=usage,
            tool_calls=len(converted.get("content") or []) - 1 if converted.get("content") else 0,
            notes=notes,
            key_id=key_id,
        )
        req_log(
            f"POST /v1/messages 200 {int((time.time() - started) * 1000)}ms {meta}"
            f" stop={converted['stop_reason']}"
        )
        return JSONResponse(converted)

    @router.post("/v1/messages/count_tokens")
    async def count_tokens(request: Request, _: None = Depends(require_key)):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        return {"input_tokens": estimate_tokens(body)}

    app.include_router(router)
