"""最小夹具：本轮只需要 mock 上游 + 建表。

（完整测试套件在 2026-10-26 的目录清理中丢失，可按需重建。）
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


class MockState:
    def __init__(self) -> None:
        self.calls = 0
        self.last: dict[str, Any] | None = None
        self.token = "test-jwt"


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-v4-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def build_mock(state: MockState) -> FastAPI:
    app = FastAPI()

    @app.get("/api/config")
    async def config():
        return JSONResponse({"name": "MockOWUI", "version": "0.5.14"})

    @app.get("/api/v1/auths/")
    async def session(request: Request):
        if request.headers.get("authorization") != f"Bearer {state.token}":
            return JSONResponse({"detail": "Not authenticated"}, status_code=403)
        return JSONResponse({"id": "u1", "email": "1023005562", "name": "Mock", "role": "user"})

    @app.get("/api/models")
    async def models():
        return JSONResponse({"data": [{"id": "deepseek-v4-flash", "owned_by": "openai"}]})

    @app.post("/api/chat/completions")
    async def chat(request: Request):
        if request.headers.get("authorization") != f"Bearer {state.token}":
            return JSONResponse({"detail": "Not authenticated"}, status_code=403)
        state.calls += 1
        body = await request.json()
        state.last = body
        text = "你好，我是 mock 上游。"
        if body.get("stream"):

            async def gen():
                for chunk in (_chunk({"content": text[:4]}), _chunk({"content": text[4:]}), _chunk({}, "stop")):
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            }
        )

    return app
