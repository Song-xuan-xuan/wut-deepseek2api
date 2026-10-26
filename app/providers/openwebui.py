"""学校 OpenWebUI 的 Provider 实现：对话、模型、会话、登录、熔断。"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from ..core.logging import log
from ..core.settings import UpstreamSettings, decode_jwt, read_token
from .base import ProviderError

RETRY_STATUS = {429, 502, 503, 504}

# 该校 signin 只校验学号是否存在，不校验密码；密码字段只要非空
PLACEHOLDER_PASSWORD = "whut-2api"


def describe(exc: Exception) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class OpenWebUIProvider:
    name = "openwebui"

    def __init__(self, settings: UpstreamSettings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.base_url = settings.base_url
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------ 基础

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.base_url,
                timeout=httpx.Timeout(
                    connect=20.0,
                    read=max(self.settings.timeout_ms / 1000, 30.0),
                    write=60.0,
                    pool=30.0,
                ),
                transport=self._transport,
                # httpx 默认读 HTTP_PROXY/ALL_PROXY，会把校园地址导去本机代理，
                # 而学校边缘会拒绝代理出口 IP —— 必须直连。
                trust_env=False,
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        headers = {"User-Agent": "whut-2api/0.4", "Accept": accept}
        token = read_token(self.settings)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.settings.cookie:
            headers["Cookie"] = self.settings.cookie
        return headers

    @staticmethod
    def _path(path: str) -> str:
        return path if path.startswith("/") else f"/{path}"

    # ------------------------------------------------------------ 熔断

    def circuit(self) -> dict[str, Any]:
        remaining = max(0.0, self._open_until - time.monotonic())
        return {
            "open": remaining > 0,
            "remaining_seconds": round(remaining, 1),
            "consecutive_failures": self._failures,
            "threshold": self.settings.circuit_breaker_threshold,
            "cooldown_seconds": self.settings.circuit_breaker_seconds,
        }

    def _check_circuit(self) -> None:
        if not self._open_until:
            return
        remaining = self._open_until - time.monotonic()
        if remaining > 0:
            raise ProviderError(
                503, f"上游连续 {self._failures} 次不可达，熔断冷却中（{int(remaining)} 秒后自动恢复）"
            )
        self._open_until = 0.0
        self._failures = 0

    def _note_failure(self) -> None:
        self._failures += 1
        threshold = self.settings.circuit_breaker_threshold
        if threshold and self._failures >= threshold:
            self._open_until = time.monotonic() + max(1, self.settings.circuit_breaker_seconds)
            log(f"上游连续 {self._failures} 次不可达，熔断 {self.settings.circuit_breaker_seconds}s")

    def _note_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        delay = 0.35 * (attempt + 1) + random.random() * 0.2
        if retry_after:
            try:
                delay = min(max(float(retry_after), 0.0), 10.0)
            except ValueError:
                pass
        await asyncio.sleep(delay)

    # ------------------------------------------------------------ 请求

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        json: Any = None,
        accept: str = "application/json",
        retries: int | None = None,
        timeout: float | None = None,
        auth: bool = True,
    ) -> httpx.Response:
        self._check_circuit()
        retries = self.settings.retries if retries is None else retries
        last: ProviderError | None = None

        for attempt in range(retries + 1):
            headers = self._headers(accept)
            if not auth:
                headers.pop("Authorization", None)
            try:
                resp = await self.client.request(
                    method,
                    self._path(path),
                    json=json,
                    headers=headers,
                    timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as exc:
                self._note_failure()
                last = ProviderError(502, f"连不上上游 {self.base_url}（{describe(exc)}）")
            else:
                self._note_success()
                if resp.status_code not in RETRY_STATUS or attempt == retries:
                    return resp
                last = ProviderError(resp.status_code, f"上游 {resp.status_code}，已重试 {attempt + 1} 次")
                retry_after = resp.headers.get("retry-after")
                await resp.aread()
                await self._sleep(attempt, retry_after)
                continue
            await self._sleep(attempt)
        raise last or ProviderError(502, "上游请求失败")

    async def chat(self, body: dict[str, Any]) -> httpx.Response:
        """发起对话，不读取响应体（流式由调用方处理）。"""
        self._check_circuit()
        accept = "text/event-stream" if body.get("stream") else "application/json"
        retries = self.settings.retries
        last: ProviderError | None = None

        for attempt in range(retries + 1):
            request = self.client.build_request(
                "POST",
                self._path(self.settings.chat_path),
                json=body,
                headers=self._headers(accept),
            )
            try:
                resp = await self.client.send(request, stream=True)
            except httpx.HTTPError as exc:
                self._note_failure()
                last = ProviderError(502, f"连不上上游 {self.base_url}（{describe(exc)}）")
            else:
                self._note_success()
                if resp.status_code not in RETRY_STATUS or attempt == retries:
                    return resp
                last = ProviderError(resp.status_code, f"上游 {resp.status_code}，已重试 {attempt + 1} 次")
                retry_after = resp.headers.get("retry-after")
                await resp.aclose()
                await self._sleep(attempt, retry_after)
                continue
            await self._sleep(attempt)
        raise last or ProviderError(502, "上游请求失败")

    # ------------------------------------------------------------ 端点

    async def list_models(self) -> Any:
        resp = await self.request(self.settings.models_path)
        if resp.status_code >= 400:
            raise ProviderError(resp.status_code, "模型列表获取失败")
        return resp.json()

    async def version(self) -> dict[str, Any] | None:
        try:
            resp = await self.request(self.settings.config_path, timeout=15.0)
        except ProviderError:
            return None
        if resp.status_code >= 400:
            return None
        data = resp.json()
        return {"name": data.get("name"), "version": data.get("version")}

    async def session(self) -> dict[str, Any]:
        resp = await self.request(self.settings.session_path, timeout=20.0)
        if resp.status_code >= 400:
            raise ProviderError(resp.status_code, "凭证校验失败")
        data = resp.json()
        return {
            "id": data.get("id"),
            "email": data.get("email"),
            "name": data.get("name"),
            "role": data.get("role"),
        }

    async def signin(self, email: str, password: str = "") -> str:
        """用学号换一份新 token。

        该校 signin 不校验密码，密码只要非空；将来若收紧，把真实密码传进来即可。
        """
        resp = await self.request(
            "/api/v1/auths/signin",
            method="POST",
            json={"email": email, "password": password or PLACEHOLDER_PASSWORD},
            retries=0,
            timeout=30.0,
            auth=False,
        )
        if resp.status_code >= 400:
            detail: Any
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:200]
            if isinstance(detail, list):
                detail = "; ".join(str(item.get("msg")) for item in detail if isinstance(item, dict))
            raise ProviderError(resp.status_code, f"认证被拒（{resp.status_code}）：{detail}")
        try:
            token = resp.json().get("token") or ""
        except ValueError as exc:
            raise ProviderError(502, "认证返回了非 JSON") from exc
        if not token:
            raise ProviderError(502, "认证返回里没有 token")
        return token

    # ------------------------------------------------------------ 状态

    def token_status(self) -> dict[str, Any]:
        token = read_token(self.settings)
        status: dict[str, Any] = {"present": bool(token), "length": len(token)}
        jwt = decode_jwt(token) or {}
        status.update(jwt)
        if not token:
            status["hint"] = "点面板上的「一键认证」用学号登录，或手动粘贴 token"
        elif jwt.get("expiring_soon"):
            status["hint"] = f"token 将在 {jwt['days_left']} 天后过期"
        return status
