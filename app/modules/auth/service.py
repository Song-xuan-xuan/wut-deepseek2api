"""认证服务：保存 token、按学号登录、临期自动续期。

关于"自动续期"的事实依据（实测，2026-11）：
    POST /api/v1/auths/signin  {"email": "<学号>", "password": "<任意非空>"}
    → 200 {"token": "..."}      该校不校验密码，只校验学号存在
因此只要知道学号就能续期，不需要保存密码。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from ...core.logging import log
from ...core.paths import shorten_path
from ...core.settings import Settings, decode_jwt, expand_home, read_token, save_email
from ...providers.base import Provider, ProviderError
from .events import AuthEventStore

PLACEHOLDER_PASSWORD = "whut-2api"


def _normalize(token: str) -> str:
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:]
    return value.strip()


def write_token(path: str, token: str) -> str:
    """原子写入 token，权限 0600。"""
    target = Path(expand_home(path)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".token-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_normalize(token) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(target, 0o600)
    return str(target)


def credentials(settings: Settings) -> tuple[str, str]:
    """返回 (学号, 密码)。环境变量 > config.json > 凭据文件。"""
    refresh = settings.token_refresh
    email = (os.environ.get("WHUT_EMAIL") or refresh.email or "").strip()
    password = os.environ.get("WHUT_PASSWORD") or refresh.password or PLACEHOLDER_PASSWORD
    return email, password


def fingerprint(token: str) -> str:
    """给 token 一个可读指纹，方便确认"现在用的是哪一份"。"""
    token = _normalize(token)
    if len(token) < 24:
        return token[:6] + "…" if token else ""
    return f"{token[:10]}…{token[-6:]}"


class AuthService:
    def __init__(self, settings: Settings, provider: Provider, events: AuthEventStore | None = None):
        self.settings = settings
        self.provider = provider
        self.events = events
        self.last_attempt: float | None = None
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.refresh_count = 0
        self._lock = asyncio.Lock()
        # 连续失败退避：避免把学号反复撞学校风控
        self._failures = 0
        self._blocked_until = 0.0

    # ------------------------------------------------------------ 退避

    def cooldown(self) -> dict[str, Any]:
        remaining = max(0.0, self._blocked_until - time.monotonic())
        return {"blocked": remaining > 0, "remaining_seconds": round(remaining, 1), "failures": self._failures}

    def _note_failure(self, message: str) -> None:
        self._failures += 1
        # 连续 3 次失败后冷却 60s，5 次后 300s（封顶）
        if self._failures >= 3:
            seconds = 60 if self._failures < 5 else 300
            self._blocked_until = time.monotonic() + seconds
            log(f"认证连续失败 {self._failures} 次，冷却 {seconds}s：{message}")

    def _note_success(self) -> None:
        self._failures = 0
        self._blocked_until = 0.0

    def _check_cooldown(self) -> None:
        remaining = self._blocked_until - time.monotonic()
        if remaining > 0:
            raise ProviderError(429, f"认证失败次数过多，请 {int(remaining) + 1} 秒后再试")

    # ------------------------------------------------------------ 状态

    @property
    def email(self) -> str:
        return credentials(self.settings)[0]

    @property
    def enabled(self) -> bool:
        return bool(self.settings.token_refresh.enabled and self.email)

    def days_left(self) -> float | None:
        return (decode_jwt(read_token(self.settings.upstream)) or {}).get("days_left")

    def should_refresh(self) -> bool:
        if not self.enabled:
            return False
        days = self.days_left()
        if days is None:
            return True
        return days < self.settings.token_refresh.refresh_before_days

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "email": self.email or None,
            "school_url": self.settings.upstream.base_url,
            "auto_refresh": self.settings.token_refresh.enabled,
            "refresh_before_days": self.settings.token_refresh.refresh_before_days,
            "check_interval_minutes": self.settings.token_refresh.check_interval_minutes,
            "refresh_count": self.refresh_count,
            "last_attempt": _iso(self.last_attempt),
            "last_success": _iso(self.last_success),
            "last_error": self.last_error,
            "fingerprint": fingerprint(read_token(self.settings.upstream)),
            "cooldown": self.cooldown(),
        }

    # ------------------------------------------------------------ 动作

    async def set_email(self, email: str) -> dict[str, Any]:
        """保存学号到 config.json，之后重启也记得。"""
        save_email(self.settings, email.strip())
        return self.status()

    async def save_token(self, token: str) -> dict[str, Any]:
        """手动粘贴 token 的路径：先落盘，再验证能否用。"""
        token = _normalize(token)
        if not token:
            raise ProviderError(400, "token 不能为空")
        write_token(self.settings.upstream.token_file, token)
        try:
            user = await self.provider.session()
        except ProviderError as exc:
            self._event("粘贴 token", False, detail=exc.message, source="面板")
            raise ProviderError(401, f"这份 token 用不了：{exc.message}") from exc
        self._event("粘贴 token", True, email=str(user.get("email") or ""), source="面板")
        return {
            "token": self.provider.token_status(),
            "user": user,
            "fingerprint": fingerprint(token),
            "email_mismatch": bool(
                self.email and user.get("email") and str(user["email"]) != self.email
            ),
        }

    async def login(self, email: str = "", password: str = "") -> dict[str, Any]:
        """用学号换一份新 token（学校自己的认证接口，无需密码）。"""
        email = (email or self.email).strip()
        if not email:
            raise ProviderError(400, "需要学号：可以先提交学号，或直接调用 /api/auth/email")
        password = password or (credentials(self.settings)[1] if email == self.email else PLACEHOLDER_PASSWORD)
        self._check_cooldown()
        async with self._lock:
            self.last_attempt = time.time()
            try:
                token = await self.provider.signin(email, password)
            except ProviderError as exc:
                self.last_error = exc.message
                self._note_failure(exc.message)
                self._event("登录", False, email=email, detail=exc.message, source="面板")
                log(f"认证失败：{exc.message}")
                raise
            path = write_token(self.settings.upstream.token_file, token)
            self.last_success = time.time()
            self.last_error = None
            self.refresh_count += 1
            self._note_success()
            save_email(self.settings, email)
            self._event("登录", True, email=email, source="面板")
            jwt = decode_jwt(token) or {}
            log(f"认证成功（{email}）→ {path}，{jwt.get('days_left')} 天后过期")
            return {"token": self.provider.token_status(), "path": shorten_path(path)}

    async def refresh(self, reason: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            token = await self.provider.signin(*credentials(self.settings))
        except (ProviderError, Exception) as exc:  # noqa: BLE001 - 续期失败不该打挂服务
            self.last_error = getattr(exc, "message", str(exc))
            self._note_failure(self.last_error)
            self._event("续期", False, email=self.email, detail=self.last_error, source=reason or "定时")
            log(f"token 续期失败（{reason or '定时'}）：{self.last_error}")
            return False

        try:
            write_token(self.settings.upstream.token_file, token)
        except OSError as exc:
            self.last_error = f"写入 {shorten_path(self.settings.upstream.token_file)} 失败：{exc.strerror or exc}"
            self._event("续期", False, email=self.email, detail=self.last_error, source=reason or "定时")
            return False

        jwt = decode_jwt(token) or {}
        self.last_success = time.time()
        self.last_error = None
        self.refresh_count += 1
        self._note_success()
        self._event(
            "续期", True, email=self.email,
            detail=f"剩余 {jwt.get('days_left')} 天", source=reason or "定时",
        )
        log(f"token 已续期（{reason or '定时'}），{jwt.get('days_left')} 天后过期")
        return True

    def _event(self, kind: str, ok: bool, **kwargs: Any) -> None:
        if self.events is not None:
            self.events.record(kind, ok, **kwargs)

    async def loop(self) -> None:
        interval = max(self.settings.token_refresh.check_interval_minutes, 1) * 60
        while True:
            try:
                if self.should_refresh():
                    await self.refresh("临期检查")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                log(f"续期循环异常：{self.last_error}")
            await asyncio.sleep(interval)


def _iso(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


__all__ = ["AuthService", "credentials", "write_token"]
