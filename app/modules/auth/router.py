"""认证模块的 HTTP 接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ...core.settings import Settings
from ...providers.base import Provider
from ..shared import error_payload, error_response, make_guard
from .service import AuthService


def attach(app: FastAPI, settings: Settings, provider: Provider, auth: AuthService) -> None:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    guard = make_guard(settings, settings.client_api_key)

    def blocked(request: Request) -> JSONResponse | None:
        return None if guard(request) else error_response("需要本地 API key", 401)

    def payload(user: dict[str, Any] | None = None) -> dict[str, Any]:
        stats = auth.events.list(15) if auth.events else []
        return {
            "token": provider.token_status(),
            "auth": auth.status(),
            "user": user,
            "school_url": settings.upstream.base_url,
            "events": stats,
            "last_success": auth.events.last_success() if auth.events else None,
        }

    @router.get("/status")
    async def status(request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        user = None
        if provider.token_status().get("present"):
            try:
                user = await provider.session()
            except Exception as exc:  # noqa: BLE001 - 状态接口不该 500
                user = {"error": getattr(exc, "message", str(exc))}
        return payload(user)

    @router.post("/email")
    async def set_email(request: Request) -> Any:
        """保存学号（写入 config.json）。"""
        if (resp := blocked(request)) is not None:
            return resp
        body = await _json(request)
        email = str(body.get("email") or "").strip()
        if not email:
            return error_response("email 不能为空", 400)
        await auth.set_email(email)
        return payload()

    @router.post("/login")
    async def login(request: Request) -> Any:
        """用学号一键认证（调用学校 signin，不校验密码）。"""
        if (resp := blocked(request)) is not None:
            return resp
        body = await _json(request)
        try:
            result = await auth.login(str(body.get("email") or ""), str(body.get("password") or ""))
        except Exception as exc:  # noqa: BLE001
            message = getattr(exc, "message", str(exc))
            status = getattr(exc, "status", 400) or 400
            if status == 429:
                return JSONResponse({**error_payload(message, 429), **payload()}, status_code=429)
            return JSONResponse({**error_payload(message, 400), **payload()}, status_code=400)
        user = None
        try:
            user = await provider.session()
        except Exception:  # noqa: BLE001
            user = None
        return {**result, **payload(user), "ok": True}

    @router.post("/token")
    async def set_token(request: Request) -> Any:
        """手动粘贴 token（浏览器登录后从 DevTools 复制）。"""
        if (resp := blocked(request)) is not None:
            return resp
        body = await _json(request)
        try:
            result = await auth.save_token(str(body.get("token") or ""))
        except Exception as exc:  # noqa: BLE001
            message = getattr(exc, "message", str(exc))
            return JSONResponse({**error_payload(message, 401), **payload()}, status_code=401)
        return {**result, **payload(result.get("user")), "ok": True}

    @router.get("/events")
    async def events_list(request: Request, limit: int = 30) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        items = auth.events.list(limit) if auth.events else []
        return {"items": items}

    @router.post("/events/clear")
    async def events_clear(request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        removed = auth.events.clear() if auth.events else 0
        return {"ok": True, "removed": removed}

    @router.post("/refresh")
    async def refresh(request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        if not auth.enabled:
            return error_response("还没有学号，先在面板里填学号并认证", 400)
        ok = await auth.refresh("手动")
        if not ok:
            return JSONResponse({**error_payload(auth.last_error or "续期失败", 502), **payload()}, status_code=502)
        return {**payload(), "ok": True}

    app.include_router(router)

    @app.get("/auth/school")
    async def go_school() -> RedirectResponse:
        """「打开学校登录页」按钮：直接跳转到学校站点。"""
        return RedirectResponse(settings.upstream.base_url, status_code=302)


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}
