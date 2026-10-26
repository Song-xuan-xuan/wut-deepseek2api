"""健康检查 / 首页 / 前端静态资源。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ...core.paths import ROOT, shorten_path
from ...core.settings import Settings
from ...providers.base import Provider

DIST = ROOT / "web" / "dist"


def attach(app: FastAPI, settings: Settings, provider: Provider) -> None:
    store = app.state.store
    auth = app.state.auth
    req_log = app.state.req_log

    # 面板每 5 秒轮询一次 /health，不能每次都去敲三遍上游：
    # 结果缓存 10 秒；熔断打开时直接返回，避免在断网时排队 20 多秒。
    health_cache: dict[str, Any] = {"at": 0.0, "payload": None}
    HEALTH_TTL = 10.0

    @app.get("/health")
    async def health() -> JSONResponse:
        started = time.time()
        cached = health_cache["payload"]
        if cached and time.time() - health_cache["at"] < HEALTH_TTL:
            token_now = provider.token_status()
            cached = {**cached, "token": token_now, "cached": True}
            return JSONResponse(cached, status_code=200 if cached["status"] == "ok" else 503)

        if provider.circuit().get("open"):
            token_now = provider.token_status()
            payload = {
                "status": "degraded",
                "service": "whut-2api",
                "version": __import__("app").__version__,
                "upstream": settings.upstream.base_url,
                "token": token_now,
                "circuit": provider.circuit(),
                "checks": {"upstream": False, "auth": False, "models": False},
                "error": "上游熔断冷却中，暂停探测",
            }
            health_cache.update(at=time.time(), payload=payload)
            return JSONResponse(payload, status_code=503)

        token = provider.token_status()
        result: dict[str, Any] = {
            "status": "ok",
            "service": "whut-2api",
            "version": __import__("app").__version__,
            "upstream": settings.upstream.base_url,
            "token": token,
            "checks": {},
        }

        async def check_upstream() -> dict | None:
            return await provider.version(retries=0, timeout=6.0)

        async def check_auth() -> dict:
            try:
                return {"user": await provider.session(retries=0, timeout=6.0)}
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status", 0) in (401, 403) and await auth.refresh("健康检查 401/403"):
                    try:
                        return {"user": await provider.session(retries=0, timeout=6.0), "refreshed": True}
                    except Exception as retry_exc:  # noqa: BLE001
                        return {"error": getattr(retry_exc, "message", str(retry_exc))}
                return {"error": getattr(exc, "message", str(exc))}

        async def check_models() -> dict:
            try:
                # 健康检查直连 provider：不走 /v1/models 的缓存与重试，别让断网时排队几十秒
                return {"models": await provider.list_models(retries=0, timeout=6.0)}
            except Exception as exc:  # noqa: BLE001
                return {"error": getattr(exc, "message", str(exc))}

        import asyncio

        info, auth_result, models_result = await asyncio.gather(
            check_upstream(), check_auth(), check_models()
        )

        result["circuit"] = provider.circuit()
        result["checks"]["upstream"] = bool(info)
        if info:
            result["upstream_info"] = {"name": info.get("name"), "version": info.get("version")}
        result["checks"]["auth"] = "user" in auth_result
        if "user" in auth_result:
            result["user"] = auth_result["user"]
        else:
            result["auth_error"] = auth_result.get("error")
        result["checks"]["models"] = "models" in models_result
        if "models" in models_result:
            result["models"] = [m["id"] for m in models_result["models"]["data"]]
        else:
            result["models_error"] = models_result.get("error")

        if not all(result["checks"].values()):
            result["status"] = "degraded"
        result["token_refresh"] = auth.status()
        result["usage"] = {
            "available": store.available,
            "today": store.summary("today")["totals"],
            "last_24h": store.summary("24h")["totals"],
            # 仪表盘的 24 小时折线从这里取（前端优先用 /health，缺失时才会退回 usage?range=7d 的日粒度数据）
            "series_24h": store.series("24h"),
            "db_path": shorten_path(store.db_path),
        }
        if token.get("expiring_soon"):
            result.setdefault("warnings", []).append(token.get("hint"))
        if auth.enabled and auth.last_error:
            result.setdefault("warnings", []).append(f"自动续期失败：{auth.last_error}")
        if not auth.enabled:
            result.setdefault("warnings", []).append("还没有学号，点面板「一键认证」即可")

        health_cache.update(at=time.time(), payload=result)
        req_log(f"GET /health {result['status']} {int((time.time() - started) * 1000)}ms")
        return JSONResponse(result, status_code=200 if result["status"] == "ok" else 503)

    @app.get("/api/system/info")
    async def system_info(request: Request) -> Any:
        allowed = app.state.allowed
        if not allowed(request):
            return JSONResponse({"error": {"message": "需要本地 API key", "code": 401}}, status_code=401)
        models: list[str] = []
        models_error = None
        try:
            payload = await app.state.get_models()
            models = [m["id"] for m in payload["data"]]
        except Exception as exc:  # noqa: BLE001
            models_error = getattr(exc, "message", str(exc))
        listen = f"{settings.listen.host}:{settings.listen.port}"
        # 容器里监听 0.0.0.0，但用户是在宿主机浏览器里访问，给个能直接点开的地址
        display_host = "127.0.0.1" if settings.listen.host in ("0.0.0.0", "::") else settings.listen.host
        display_listen = f"{display_host}:{settings.listen.port}"
        loopback_only = settings.listen.host in ("127.0.0.1", "localhost", "::1")
        return {
            "service": {
                "name": "whut-2api",
                "version": __import__("app").__version__,
                "repo_url": settings.repo_url,
                "disabled_models": settings.upstream.disabled_models,
                "model_guard": app.state.model_guard.snapshot(),
                "panel_key_needed": not loopback_only,
                "upstream": settings.upstream.base_url,
                "listen": display_listen,
                "listen_raw": listen,
                "client_api_key": settings.client_api_key,
                "default_model": settings.default_model,
                "max_tokens_cap": settings.max_tokens_cap,
                "keep_alive_ms": settings.keep_alive_ms,
                "config_path": shorten_path(settings.config_path),
                "token_file": shorten_path(settings.upstream.token_file),
            },
            "token": provider.token_status(),
            "auth": auth.status(),
            "circuit": provider.circuit(),
            "models": models,
            "models_error": models_error,
            "usage": {
                "available": store.available,
                "db_path": shorten_path(store.db_path),
                "total_rows": store.total_count(),
                # 仪表盘的 KPI 与 24h 折线都从这里取，避免前端再发两个请求
                "today": store.summary("today")["totals"],
                "last_24h": store.summary("24h")["totals"],
                "series_24h": store.series("24h"),
            },
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    @app.get("/")
    async def index() -> Any:
        """根路径直接进面板。

        不管是从 OrbStack 的 127.0.0.1 链接、`*.orb.local` 域名还是收藏夹进来，
        都落到能用的 UI 上，而不是给一段容易被当成“打不开”的纯文本。
        """
        return RedirectResponse("/dashboard", status_code=307)

    @app.get("/api/endpoints", response_class=PlainTextResponse)
    async def endpoints() -> PlainTextResponse:
        """纯文本接口清单（给 curl / 忘记地址时看）。"""
        return PlainTextResponse(
            "\n".join(
                [
                    "whut-2api — 把学校 OpenWebUI 包装成 OpenAI 兼容接口",
                    "",
                    f"面板      http://{settings.listen.host}:{settings.listen.port}/dashboard",
                    "GET  /v1/models            模型列表",
                    "POST /v1/chat/completions  对话补全（stream / tools 均支持）",
                    "POST /v1/messages          Anthropic 协议（Claude Code / Anthropic SDK）",
                    "POST /v1/responses         Responses 协议（Codex CLI）",
                    "GET  /health               自检",
                    "GET  /api/auth/status      认证状态",
                    "GET  /api/access/overview  分发 key / 客户端配置",
                    "GET  /api/usage/overview   用量统计",
                    "GET  /docs                 OpenAPI 文档",
                    "",
                    f"Auth: Authorization: Bearer {settings.client_api_key or '(未设置)'}",
                ]
            )
            + "\n"
        )

    # 面板：优先用 Vite 构建产物，没有就回退到提示页
    if (DIST / "index.html").is_file():
        if (DIST / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

        @app.get("/dashboard")
        async def dashboard() -> FileResponse:
            return FileResponse(DIST / "index.html")

        # Vite 会把 web/public/* 复制到 dist 根目录（架构图、截图、logo）
        ALLOWED_ASSET_SUFFIXES = {".svg", ".png", ".jpg", ".webp", ".ico"}

        @app.get("/{asset:path}")
        async def static_asset(asset: str) -> Any:
            candidate = (DIST / asset).resolve()
            try:
                candidate.relative_to(DIST.resolve())
            except (ValueError, OSError):
                raise HTTPException(status_code=404) from None
            if not candidate.is_file() or candidate.suffix not in ALLOWED_ASSET_SUFFIXES:
                raise HTTPException(status_code=404)
            return FileResponse(candidate)

        @app.get("/favicon.ico")
        async def favicon():
            icon = DIST / "favicon.svg"
            if icon.is_file():
                return FileResponse(icon)
            return PlainTextResponse("", status_code=204)
    else:

        @app.get("/dashboard")
        async def dashboard_missing() -> PlainTextResponse:
            return PlainTextResponse(
                "面板还没构建。在 web/ 目录执行：npm install && npm run build\n"
                "（开发模式：npm run dev，然后访问 http://127.0.0.1:5173）\n",
                status_code=503,
            )
