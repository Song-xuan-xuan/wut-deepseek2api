"""配置加载、token 热加载、JWT 解析。

优先级：环境变量 > config.json > 内置默认值。
token 文件按 mtime 缓存，外部改写后无需重启服务。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import data_dir, migrate_legacy, resolve_path

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class ListenSettings:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass
class UpstreamSettings:
    base_url: str = "https://chatcloud.whut.edu.cn"
    chat_path: str = "/api/chat/completions"
    models_path: str = "/api/models"
    session_path: str = "/api/v1/auths/"
    config_path: str = "/api/config"
    token_file: str = "data/token"
    credentials_file: str = "data/credentials.json"
    token: str = ""
    cookie: str = ""
    timeout_ms: int = 600_000
    retries: int = 2
    circuit_breaker_threshold: int = 3
    circuit_breaker_seconds: int = 60


@dataclass
class TokenRefreshSettings:
    enabled: bool = True
    email: str = ""
    password: str = ""
    refresh_before_days: float = 2.0
    check_interval_minutes: int = 60


@dataclass
class UsageSettings:
    """用量统计（SQLite 持久化）。"""

    enabled: bool = True
    db_path: str = ""  # 空 = <项目>/data/usage.db
    retention_days: int = 180


@dataclass
class AccessSettings:
    """分发：本地 key 的默认策略。"""

    enabled: bool = True
    default_rpm_limit: int = 120
    default_daily_token_limit: int = 0  # 0 = 不限
    expose_network: bool = True


@dataclass
class Settings:
    listen: ListenSettings = field(default_factory=ListenSettings)
    upstream: UpstreamSettings = field(default_factory=UpstreamSettings)
    token_refresh: TokenRefreshSettings = field(default_factory=TokenRefreshSettings)
    usage: UsageSettings = field(default_factory=UsageSettings)
    access: AccessSettings = field(default_factory=AccessSettings)
    default_model: str = ""
    client_api_key: str = ""
    max_tokens_cap: int = 32_768
    strip_fields: list[str] = field(
        default_factory=lambda: ["store", "metadata", "service_tier", "safety_identifier"]
    )
    enable_native_tool_calling: bool = False
    keep_alive_ms: int = 8_000
    log_requests: bool = True
    models: list[Any] = field(default_factory=list)
    config_path: str = ""


def _build(cls, data: dict[str, Any] | None):
    """只取 dataclass 认识的键，避免配置里多一个字段就崩。"""
    data = data or {}
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def _snake_case(key: str) -> str:
    """camelCase → snake_case：兼容早期 Node 版配置，两种写法都能用。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _normalize_keys(data):
    if isinstance(data, dict):
        return {_snake_case(k): _normalize_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_normalize_keys(item) for item in data]
    return data


def expand_home(path: str) -> str:
    """兼容旧调用：展开 ~ 并把相对路径按项目根目录解析。"""
    return resolve_path(path) if path else path


def load_settings(path: str | None = None) -> Settings:
    config_path = path or os.environ.get("WHUT_CONFIG") or str(ROOT / "config.json")
    raw: dict[str, Any] = {}
    file = Path(config_path)
    if file.is_file():
        try:
            raw = _normalize_keys(json.loads(file.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"config.json 解析失败：{exc}") from exc

    merged: dict[str, Any] = dict(raw)

    # ---- 环境变量覆盖
    env = os.environ
    ups = merged.setdefault("upstream", {})
    listen = merged.setdefault("listen", {})
    if env.get("WHUT_BASE_URL"):
        ups["base_url"] = env["WHUT_BASE_URL"]
    if env.get("WHUT_TOKEN"):
        ups["token"] = env["WHUT_TOKEN"]
    if env.get("WHUT_TOKEN_FILE"):
        ups["token_file"] = env["WHUT_TOKEN_FILE"]
    if env.get("WHUT_PORT"):
        listen["port"] = int(env["WHUT_PORT"])
    if env.get("WHUT_HOST"):
        listen["host"] = env["WHUT_HOST"]
    if env.get("WHUT_CLIENT_KEY") is not None:
        merged["client_api_key"] = env["WHUT_CLIENT_KEY"]
    if env.get("WHUT_MAX_TOKENS_CAP"):
        merged["max_tokens_cap"] = int(env["WHUT_MAX_TOKENS_CAP"])
    if env.get("WHUT_EMAIL"):
        merged.setdefault("token_refresh", {})["email"] = env["WHUT_EMAIL"]

    upstream = _build(UpstreamSettings, merged.get("upstream"))
    upstream.base_url = upstream.base_url.rstrip("/")

    settings = _build(
        Settings,
        {**{k: v for k, v in merged.items() if k != "upstream"}, "upstream": upstream},
    )
    settings.listen = _build(ListenSettings, merged.get("listen"))
    settings.upstream = upstream
    settings.token_refresh = _build(TokenRefreshSettings, merged.get("token_refresh"))
    settings.usage = _build(UsageSettings, merged.get("usage"))
    settings.access = _build(AccessSettings, merged.get("access"))
    if not settings.usage.db_path:
        settings.usage.db_path = str(data_dir() / "usage.db")

    for record in migrate_legacy():
        print(f"[迁移] 旧数据已搬到项目目录：{record}", flush=True)

    settings.upstream.token_file = resolve_path(settings.upstream.token_file)
    settings.upstream.credentials_file = resolve_path(settings.upstream.credentials_file)
    settings.usage.db_path = resolve_path(settings.usage.db_path)
    settings.config_path = config_path
    return settings


# ---------------------------------------------------------------- token

_token_cache: dict[str, Any] = {"path": "", "mtime": 0.0, "token": ""}


def _normalize(raw: str | None) -> str:
    value = (raw or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:]
    return value.strip()


def read_token(upstream: UpstreamSettings) -> str:
    env_token = _normalize(os.environ.get("WHUT_TOKEN"))
    if env_token:
        return env_token

    path = expand_home(upstream.token_file)
    if path and os.path.isfile(path):
        mtime = os.path.getmtime(path)
        if _token_cache["path"] != path or _token_cache["mtime"] != mtime:
            _token_cache.update(
                path=path,
                mtime=mtime,
                token=_normalize(Path(path).read_text(encoding="utf-8")),
            )
        if _token_cache["token"]:
            return _token_cache["token"]

    return _normalize(upstream.token)


def decode_jwt(token: str) -> dict[str, Any] | None:
    """只解 payload 的 exp，不做签名校验（仅用于健康检查和过期预警）。"""
    parts = (token or "").split(".")
    if len(parts) < 2:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    exp = data.get("exp")
    if not exp:
        return None
    days_left = round((exp - time.time()) / 86_400, 1)
    return {
        "exp": exp,
        "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)),
        "days_left": days_left,
        "expiring_soon": days_left < 2,
    }


def token_status(upstream: UpstreamSettings) -> dict[str, Any]:
    token = read_token(upstream)
    status: dict[str, Any] = {"present": bool(token), "length": len(token)}
    jwt = decode_jwt(token) or {}
    status.update(jwt)
    if not token:
        status["hint"] = f"把浏览器里的 JWT 写入 {upstream.token_file}，或设置 WHUT_TOKEN"
    elif jwt.get("expiring_soon"):
        status["hint"] = f"token 将在 {jwt['days_left']} 天后过期，请更新 {upstream.token_file}"
    return status


def save_email(settings: Settings, email: str) -> Settings:
    """把学号写回 config.json（保留其它字段，原子写入，权限 0600）。

    这样用户在面板里认证一次，之后重启也不用再配置。
    """
    path = Path(settings.config_path)
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
    block = raw.setdefault("token_refresh", {})
    block["email"] = email
    block.setdefault("enabled", True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)

    settings.token_refresh.email = email
    return settings
