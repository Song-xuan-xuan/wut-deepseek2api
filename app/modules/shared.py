"""模块间共用的小工具。"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def error_payload(message: str, status: int = 400, auth_hint: bool = True) -> dict[str, Any]:
    """OpenAI 风格的错误体。auth_hint=False 用于与凭证无关的 403（例如跨站请求被拒）。"""
    if status in (401, 403):
        error_type = "authentication_error"
        hint = "（凭证可能已过期，点面板的「一键认证」）" if auth_hint else ""
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


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}


def host_is_local(request: Request) -> bool:
    """用户是不是从本机地址访问的（看 Host 头）。

    Docker / OrbStack 做了端口转发，容器内看到的 `request.client.host` 是网桥地址
    （172.x / 192.168.x），不是 127.0.0.1，所以只看来源 IP 会把本机浏览器误判成远程。
    """
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    return host in LOCAL_HOSTS


def make_guard(settings, client_api_key: str):
    """面板接口的统一鉴权：本机放行；远程要求 Bearer key（或在 URL 上带 ?k=）。"""

    def allowed(request: Request) -> bool:
        if not client_api_key:
            return True
        if is_loopback(request) or host_is_local(request):
            return True
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
        if bearer == client_api_key or request.headers.get("x-api-key") == client_api_key:
            return True
        # 浏览器直接下载/跳转时带不了自定义头，允许用 ?k= 传（面板的下载链接就是这么做的）
        return request.query_params.get("k") == client_api_key

    return allowed
