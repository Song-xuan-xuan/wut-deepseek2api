"""FastAPI 应用组装。

main.py 只做三件事：
  1. 读配置、建 provider
  2. 起 auth / usage 服务，挂后台续期任务
  3. 依次挂载各功能模块的 router

业务逻辑都在 modules/ 和 providers/ 里，加新模块只需要在这里 include 一行。
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .core.db import Database
from .core.logging import log, noop
from .core.settings import Settings, load_settings
from .modules.access.router import attach as attach_access
from .modules.access.store import KeyStore
from .modules.anthropic.router import attach as attach_anthropic
from .modules.auth.events import AuthEventStore
from .modules.auth.router import attach as attach_auth
from .modules.auth.service import AuthService
from .modules.proxy.router import attach as attach_proxy
from .modules.responses.router import attach as attach_responses
from .modules.shared import error_payload, make_guard
from .modules.system.router import attach as attach_system
from .modules.usage.router import attach as attach_usage
from .modules.usage.store import UsageStore
from .providers import build_provider


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    provider = build_provider(settings, transport=transport)
    db = Database(settings.usage.db_path, enabled=settings.usage.enabled)
    store = UsageStore(db, settings.usage)
    keys = KeyStore(db)
    events = AuthEventStore(db)
    auth = AuthService(settings, provider, events)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.connect()
        keys.reload()
        task: asyncio.Task | None = None
        if auth.enabled:
            if auth.should_refresh():
                await auth.refresh("启动检查")
            task = asyncio.create_task(auth.loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await provider.aclose()
            store.close()

    app = FastAPI(
        title="whut-2api",
        version=__version__,
        description="把学校 OpenWebUI 包装成 OpenAI 兼容接口",
        lifespan=lifespan,
    )

    # ---- 共享状态
    app.state.settings = settings
    app.state.provider = provider
    app.state.upstream = provider  # 兼容旧脚本
    app.state.db = db
    app.state.store = store
    app.state.keys = keys
    app.state.events = events
    app.state.auth = auth
    app.state.refresher = auth  # 兼容旧脚本
    app.state.req_log = log if settings.log_requests else noop
    app.state.allowed = make_guard(settings, settings.client_api_key)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    class _Unauthorized(Exception):
        pass

    class _QuotaExceeded(Exception):
        def __init__(self, message: str, retry_after: int = 0):
            super().__init__(message)
            self.message = message
            self.retry_after = retry_after

    def require_key(request: Request) -> None:
        """OpenAI 接口：始终校验 key（本机也校验）。

        key 可以是 config 里的主 key，也可以是「分发」页创建的子 key；
        子 key 会被检查每日 token 配额与每分钟限流。
        """
        presented = request.headers.get("authorization", "")
        bearer = presented[7:] if presented.lower().startswith("bearer ") else ""
        candidate = bearer or request.headers.get("x-api-key", "")
        if not candidate:
            if not settings.client_api_key and not keys.list():
                return
            raise _Unauthorized()

        if settings.client_api_key and candidate == settings.client_api_key:
            request.state.key_id = 0
            return

        item = keys.resolve(candidate)
        if item is None:
            raise _Unauthorized()

        quota = keys.quota_state(item)
        if quota["exceeded"]:
            raise _QuotaExceeded(
                f"这把 key 今日 token 配额已用尽（{quota['tokens_used_today']}/{quota['daily_token_limit']}）"
            )
        allowed, retry_after = keys.check_rate(item, settings.access.default_rpm_limit)
        if not allowed:
            raise _QuotaExceeded(f"请求过于频繁（限 {item['rpm_limit'] or settings.access.default_rpm_limit} 次/分钟）", retry_after)
        keys.touch(item["id"])
        request.state.key_id = item["id"]

    @app.exception_handler(_Unauthorized)
    async def _on_unauthorized(_: Request, __: Any) -> JSONResponse:
        return JSONResponse(error_payload("缺少或错误的本地 API key", 401), status_code=401)

    @app.exception_handler(_QuotaExceeded)
    async def _on_quota(_: Request, exc: _QuotaExceeded) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
        return JSONResponse(error_payload(exc.message, 429), status_code=429, headers=headers)

    # ---- 功能模块（顺序无关）
    attach_proxy(app, settings, provider, store, require_key)
    attach_anthropic(app, settings, provider, store, require_key)
    attach_responses(app, settings, provider, store, require_key)
    attach_auth(app, settings, provider, auth)
    attach_usage(app, settings, store, keys)
    attach_access(app, settings, keys)
    attach_system(app, settings, provider)

    return app


__all__ = ["create_app"]
