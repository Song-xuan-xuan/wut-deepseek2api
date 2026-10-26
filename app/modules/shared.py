"""模块间共用的小工具。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def error_payload(message: str, status: int = 400) -> dict[str, Any]:
    """OpenAI 风格的错误体。"""
    if status in (401, 403):
        error_type, hint = "authentication_error", "（凭证可能已过期，点面板的「一键认证」）"
    elif status == 429:
        error_type, hint = "rate_limit_error", "（上游限流，降低并发或稍后重试）"
    elif status >= 500:
        error_type, hint = "server_error", ""
    else:
        error_type, hint = "invalid_request_error", ""
    return {
        "error": {"message": f"{message}{hint}", "type": error_type, "param": None, "code": status}
    }


def error_response(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(error_payload(message, status), status_code=status)


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "")


def make_guard(settings, client_api_key: str):
    """面板接口的统一鉴权：本机放行；远程要求 Bearer key。"""

    def allowed(request: Request) -> bool:
        if is_loopback(request) or not client_api_key:
            return True
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
        return bearer == client_api_key or request.headers.get("x-api-key") == client_api_key

    return allowed
