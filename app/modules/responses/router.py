"""POST /v1/responses —— 让 Codex CLI（0.1xx 起只支持 Responses API）可用。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...core.logging import log, noop
from ...core.settings import Settings
from ...providers.base import Provider, ProviderError
from ..proxy.retry import chat_with_retry
from ..proxy.transform import sanitize_chat_request
from ..usage.store import UsageStore
from .convert import (
    ResponsesStreamWriter,
    error_body,
    to_openai_request,
    to_response_object,
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

    @router.post("/v1/responses")
    async def responses(request: Request, _: None = Depends(require_key)):
        started = time.time()
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(error_body("请求体不是合法 JSON", "invalid_request_error"), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(error_body("请求体必须是 JSON 对象", "invalid_request_error"), status_code=400)

        key_id = int(getattr(request.state, "key_id", 0) or 0)
        model = str(body.get("model") or "")
        response_id = f"resp_{uuid.uuid4().hex[:24]}"

        try:
            openai_body, notes = sanitize_chat_request(to_openai_request(body), settings)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(error_body(f"请求转换失败：{exc}", "invalid_request_error"), status_code=400)

        meta = f"responses model={openai_body.get('model')} stream={bool(openai_body.get('stream'))}"
        try:
            resp, status, message = await chat_with_retry(
                app, openai_body, notes=notes, req_log=req_log, tag="responses"
            )
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

        if resp is None:
            store.record(
                model=str(openai_body.get("model") or ""),
                status=status,
                ms=(time.time() - started) * 1000,
                stream=bool(openai_body.get("stream")),
                tools=len(openai_body.get("tools") or []),
                notes=notes,
                error=message,
                key_id=key_id,
            )
            return JSONResponse(error_body(message), status_code=status)

        content_type = resp.headers.get("content-type", "")
        streaming = "event-stream" in content_type or (
            openai_body.get("stream") and "application/json" not in content_type
        )

        if streaming:
            req_log(f"POST /v1/responses 200 {int((time.time() - started) * 1000)}ms {meta} sse")

            async def events() -> AsyncIterator[bytes]:
                writer = ResponsesStreamWriter(model or str(openai_body.get("model") or ""), response_id)
                buffer = ""
                yield writer.start()
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
                                continue
                            try:
                                chunk = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            if chunk.get("error"):
                                yield b"event: response.failed\ndata: " + json.dumps(
                                    {
                                        "type": "response.failed",
                                        "response": {
                                            "id": response_id,
                                            "status": "failed",
                                            "error": {"message": str(chunk["error"]), "type": "server_error"},
                                        },
                                    },
                                    ensure_ascii=False,
                                ).encode() + b"\n\n"
                                return
                            out = writer.feed(chunk)
                            if out:
                                yield out
                    yield writer.finish()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    yield b"event: response.failed\ndata: " + json.dumps(
                        {
                            "type": "response.failed",
                            "response": {
                                "id": response_id,
                                "status": "failed",
                                "error": {"message": f"上游流中断：{exc}", "type": "server_error"},
                            },
                        },
                        ensure_ascii=False,
                    ).encode() + b"\n\n"
                finally:
                    with contextlib.suppress(Exception):
                        await resp.aclose()
                    store.record(
                        model=str(openai_body.get("model") or ""),
                        status=200,
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

        converted = to_response_object(payload, model, response_id)
        store.record(
            model=str(openai_body.get("model") or ""),
            status=200,
            ms=(time.time() - started) * 1000,
            stream=False,
            tools=len(openai_body.get("tools") or []),
            usage=payload.get("usage") or {},
            tool_calls=sum(1 for item in converted["output"] if item["type"] == "function_call"),
            notes=notes,
            key_id=key_id,
        )
        req_log(f"POST /v1/responses 200 {int((time.time() - started) * 1000)}ms {meta}")
        return JSONResponse(converted)

    app.include_router(router)
