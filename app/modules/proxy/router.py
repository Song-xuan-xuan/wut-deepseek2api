"""OpenAI 兼容路由：/v1/models 与 /v1/chat/completions。"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...core.logging import log, noop
from ...core.settings import Settings
from ...providers.base import Provider, ProviderError
from ..shared import error_response
from ..usage.store import UsageStore
from .retry import chat_with_retry
from .streaming import sse_stream
from .transform import (
    ascii_notes,
    resolve_model_alias,
    sanitize_chat_request,
    to_openai_model_list,
)

MAX_BODY_BYTES = 1024 * 1024  # 学校反代在 ~1MB 请求体处返回 413，提前拦下并说明原因
MODEL_CACHE_TTL = 60.0


def attach(
    app: FastAPI,
    settings: Settings,
    provider: Provider,
    store: UsageStore,
    require_key: Any,
) -> None:
    router = APIRouter()
    req_log = log if settings.log_requests else noop
    cache: dict[str, Any] = {"at": 0.0, "payload": None}

    async def get_models(force: bool = False) -> dict[str, Any]:
        if not force and cache["payload"] and time.time() - cache["at"] < MODEL_CACHE_TTL:
            return _visible(cache["payload"])
        try:
            raw = await provider.list_models()
        except ProviderError:
            if settings.models:
                return _visible(to_openai_model_list(None, settings.models))
            raise
        payload = to_openai_model_list(raw, settings.models)
        cache.update(at=time.time(), payload=payload)
        return _visible(payload)

    def _visible(payload: dict[str, Any]) -> dict[str, Any]:
        """剔除配置里禁用的模型，以及被看门狗临时隔离的模型（缓存里存全量）。"""
        disabled = {m for m in (settings.upstream.disabled_models or []) if m}
        guard = app.state.model_guard
        data = [
            m
            for m in payload.get("data", [])
            if m.get("id") not in disabled and not guard.is_blocked(m.get("id", ""))
        ]
        return {**payload, "data": data}

    app.state.get_models = get_models

    @router.get("/v1/models")
    async def list_models(_: None = Depends(require_key)) -> JSONResponse:
        started = time.time()
        try:
            payload = await get_models()
        except ProviderError as exc:
            return error_response(exc.message, exc.status)
        req_log(f"GET /v1/models 200 {int((time.time() - started) * 1000)}ms models={len(payload['data'])}")
        return JSONResponse(payload)

    @router.post("/v1/chat/completions")
    @router.post("/chat/completions")
    async def chat(request: Request, _: None = Depends(require_key)):
        started = time.time()
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return error_response(
                f"请求体 {len(raw)} 字节，超过上限 {MAX_BODY_BYTES} 字节"
                "（学校反向代理在约 1MB 处会返回 413，建议裁剪历史消息后再试）",
                413,
            )
        try:
            incoming = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            return error_response("请求体不是合法 JSON", 400)
        if not isinstance(incoming, dict):
            return error_response("请求体必须是 JSON 对象", 400)

        key_id = int(getattr(request.state, "key_id", 0) or 0)
        body, notes = sanitize_chat_request(incoming, settings)
        body, alias_notes = resolve_model_alias(body, settings.model_aliases)
        notes.extend(alias_notes)
        meta = (
            f"model={body.get('model')} stream={bool(body.get('stream'))} "
            f"tools={len(body.get('tools') or [])}" + (f" notes={','.join(notes)}" if notes else "")
        )

        # 上游偶发 400/403（学校把网关故障塞进这两个码）+ token 过期，统一走重试
        try:
            resp, status, message = await chat_with_retry(
                app, body, notes=notes, req_log=req_log, tag="chat"
            )
        except ProviderError as exc:
            store.record(
                model=str(body.get("model") or ""),
                status=exc.status,
                ms=(time.time() - started) * 1000,
                stream=bool(body.get("stream")),
                tools=len(body.get("tools") or []),
                notes=notes,
                error=exc.message,
                key_id=key_id,
            )
            req_log(
                f"POST /v1/chat/completions {exc.status} "
                f"{int((time.time() - started) * 1000)}ms {meta} {exc.message}"
            )
            return error_response(exc.message, exc.status)

        if resp is None:
            store.record(
                model=str(body.get("model") or ""),
                status=status,
                ms=(time.time() - started) * 1000,
                stream=bool(body.get("stream")),
                tools=len(body.get("tools") or []),
                notes=notes,
                error=message,
                key_id=key_id,
            )
            req_log(
                f"POST /v1/chat/completions {status} "
                f"{int((time.time() - started) * 1000)}ms {meta} upstream-error={message[:120]!r}"
            )
            return error_response(message, status)

        content_type = resp.headers.get("content-type", "")
        streaming = "event-stream" in content_type or (
            body.get("stream") and "application/json" not in content_type
        )
        extra_headers = {"x-whut-notes": ascii_notes(notes)} if notes else {}

        app.state.model_guard.record_success(str(body.get("model") or ""))

        if streaming:
            observed: dict[str, Any] = {"usage": None, "tool_calls": 0, "finish": None}

            def observe(chunk: dict[str, Any]) -> None:
                if chunk.get("usage"):
                    observed["usage"] = chunk["usage"]
                for choice in chunk.get("choices") or []:
                    if choice.get("finish_reason"):
                        observed["finish"] = choice["finish_reason"]
                    if (choice.get("delta") or {}).get("tool_calls"):
                        observed["tool_calls"] = max(observed["tool_calls"], 1)

            def on_finish(saw_done: bool) -> None:
                store.record(
                    model=str(body.get("model") or ""),
                    status=200 if saw_done else 206,
                    ms=(time.time() - started) * 1000,
                    stream=True,
                    tools=len(body.get("tools") or []),
                    usage=observed["usage"],
                    tool_calls=observed["tool_calls"],
                    notes=notes,
                    key_id=key_id,
                )

            req_log(f"POST /v1/chat/completions 200 {int((time.time() - started) * 1000)}ms {meta} sse")
            return StreamingResponse(
                sse_stream(resp, settings.keep_alive_ms, observe=observe, on_finish=on_finish),
                media_type="text/event-stream; charset=utf-8",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    **extra_headers,
                },
            )

        data = await resp.aread()
        await resp.aclose()
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            store.record(
                model=str(body.get("model") or ""),
                status=502,
                ms=(time.time() - started) * 1000,
                stream=False,
                tools=len(body.get("tools") or []),
                notes=notes,
                error="上游返回了非 JSON 内容",
            )
            return error_response(f"上游返回了非 JSON 内容：{data[:200]!r}", 502)

        # 上游偶尔会 200 + 空 choices（实测 deepseek-v3 收到超大输入时），
        # 直接透传会让 agent 拿到 null 崩掉，这里转成明确的 502。
        if not payload.get("choices"):
            store.record(
                model=str(body.get("model") or ""),
                status=502,
                ms=(time.time() - started) * 1000,
                stream=False,
                tools=len(body.get("tools") or []),
                notes=notes,
                error="上游返回 200 但 choices 为空",
                key_id=key_id,
            )
            return error_response(
                "上游返回 200 但没有 choices（通常是输入过大导致上游静默失败，请缩短输入或换模型）", 502
            )

        choice = payload["choices"][0] or {}
        message = choice.get("message") or {}
        # tool_choice=required 时上游会给出 finish_reason=tool_calls 却没有 tool_calls
        # （把残片塞进 content 的 DSML 标记里），这种自相矛盾的状态会让客户端报错。
        if choice.get("finish_reason") == "tool_calls" and not message.get("tool_calls"):
            choice["finish_reason"] = "stop"
            notes = [*notes, "fix-orphan-tool-calls"]

        usage = payload.get("usage") or {}
        tool_calls = len(message.get("tool_calls") or [])
        store.record(
            model=str(body.get("model") or ""),
            status=200,
            ms=(time.time() - started) * 1000,
            stream=False,
            tools=len(body.get("tools") or []),
            usage=usage,
            tool_calls=tool_calls,
            notes=notes,
            key_id=key_id,
        )
        req_log(
            f"POST /v1/chat/completions 200 {int((time.time() - started) * 1000)}ms {meta}"
            + (f" tool_calls={tool_calls}" if tool_calls else "")
            + (f" tokens={usage.get('prompt_tokens')}+{usage.get('completion_tokens')}" if usage else "")
        )
        return JSONResponse(payload, headers=extra_headers)

    app.include_router(router)
