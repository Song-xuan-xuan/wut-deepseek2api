"""分发模块的 HTTP 接口：本地 key 管理、配额、客户端配置、网络信息。"""

from __future__ import annotations

import json
import socket
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ...core.settings import Settings
from ..shared import error_payload, error_response, make_guard
from .store import KeyStore

CONFIG_TEMPLATES = ("pi", "codex", "claude-code", "continue", "curl", "python")


def _lan_addresses() -> list[str]:
    """尽力枚举本机内网 IPv4（不依赖第三方库）。"""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    # 兜底：通过 UDP 探测默认出口地址
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("223.5.5.5", 80))
            ip = sock.getsockname()[0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def attach(app: FastAPI, settings: Settings, keys: KeyStore) -> None:
    router = APIRouter(prefix="/api/access", tags=["access"])
    guard = make_guard(settings, settings.client_api_key)

    def blocked(request: Request):
        if guard(request):
            return None
        return JSONResponse(
            error_payload(
                "面板接口需要 API key：从本机 127.0.0.1 打开面板，"
                "或在 URL 上加 ?k=<config.json 里的 client_api_key>",
                401,
                auth_hint=False,
            ),
            status_code=401,
        )

    def base_url(request: Request) -> str:
        """面板/客户端应该用的地址：本机监听 + 当前端口。"""
        host = settings.listen.host
        if host in ("0.0.0.0", "::"):
            host = request.url.hostname or "127.0.0.1"
        port = settings.listen.port
        scheme = request.url.scheme or "http"
        return f"{scheme}://{host}:{port}"

    # ------------------------------------------------------------ 概览

    @router.get("/overview")
    async def overview(request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        lan = _lan_addresses() if settings.access.expose_network else []
        exposed = settings.listen.host in ("0.0.0.0", "::")
        return {
            "keys": keys.dashboard(),
            "network": {
                "listen_host": settings.listen.host,
                "port": settings.listen.port,
                "exposed": exposed,
                "lan_addresses": lan,
                "base_url": base_url(request),
                "lan_base_url": f"http://{lan[0]}:{settings.listen.port}/v1" if lan and exposed else None,
            },
            "limits": {
                "default_rpm_limit": settings.access.default_rpm_limit,
                "default_daily_token_limit": settings.access.default_daily_token_limit,
            },
            "master_key": settings.client_api_key,
            "single_key_mode": not keys.list(),
        }

    # ------------------------------------------------------------ key CRUD

    @router.post("/keys")
    async def create_key(request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        body = await _json(request)
        item = keys.create(
            str(body.get("name") or "新设备"),
            rpm_limit=int(body.get("rpm_limit") or settings.access.default_rpm_limit),
            daily_token_limit=int(body.get("daily_token_limit") or settings.access.default_daily_token_limit),
            note=str(body.get("note") or ""),
        )
        return {"ok": True, "key": _decorate(item)}

    @router.patch("/keys/{key_id}")
    async def update_key(key_id: int, request: Request) -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        body = await _json(request)
        item = keys.update(
            key_id,
            name=body.get("name"),
            enabled=body.get("enabled"),
            note=body.get("note"),
            rpm_limit=body.get("rpm_limit"),
            daily_token_limit=body.get("daily_token_limit"),
        )
        if item is None:
            return error_response("key 不存在", 404)
        return {"ok": True, "key": _decorate(item)}

    @router.post("/keys/{key_id}/rotate")
    async def rotate_key(key_id: int) -> Any:
        item = keys.rotate(key_id)
        if item is None:
            return error_response("key 不存在", 404)
        return {"ok": True, "key": _decorate(item)}

    @router.delete("/keys/{key_id}")
    async def delete_key(key_id: int) -> Any:
        if not keys.delete(key_id):
            return error_response("key 不存在", 404)
        return {"ok": True}

    # ------------------------------------------------------------ 客户端配置

    @router.get("/client-config")
    async def client_config(request: Request, kind: str = "pi", key: str = "") -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        if kind not in CONFIG_TEMPLATES:
            return error_response(f"不支持的模板：{kind}", 400)
        key = key or settings.client_api_key or (keys.list()[0]["key"] if keys.list() else "sk-whut-local")
        host = base_url(request)
        model = settings.default_model or "deepseek-v4-flash"
        return {
            "kind": kind,
            "content": _render(kind, host, key, model, settings.max_tokens_cap),
            "available": list(CONFIG_TEMPLATES),
        }

    @router.get("/client-config/download")
    async def client_config_download(request: Request, kind: str = "pi", key: str = "") -> Any:
        if (resp := blocked(request)) is not None:
            return resp
        if kind not in CONFIG_TEMPLATES:
            return error_response(f"不支持的模板：{kind}", 400)
        key = key or settings.client_api_key or "sk-whut-local"
        host = base_url(request)
        model = settings.default_model or "deepseek-v4-flash"
        content = _render(kind, host, key, model, settings.max_tokens_cap)
        filename = {
            "pi": "models.json",
            "codex": "codex-config.toml",
            "claude-code": "claude-code.sh",
            "continue": "config.json",
            "curl": "curl.sh",
            "python": "example.py",
        }.get(kind, "config.txt")
        return PlainTextResponse(
            content,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.include_router(router)


def _decorate(item: dict[str, Any]) -> dict[str, Any]:
    from .store import mask

    data = dict(item)
    data["masked"] = mask(item.get("key", ""))
    data["enabled"] = bool(item.get("enabled"))
    return data


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _render(kind: str, host: str, key: str, model: str, max_tokens: int) -> str:
    base = f"{host}/v1"
    if kind == "pi":
        return f"""// 合并进 ~/.pi/agent/models.json 的 providers 里
{{
  "whut": {{
    "baseUrl": "{base}",
    "api": "openai-completions",
    "apiKey": "{key}",
    "models": [
      {{
        "id": "{model}",
        "name": "WHUT {model}",
        "contextWindow": 131072,
        "maxTokens": {max_tokens},
        "input": ["text", "image"],
        "reasoning": false,
        "compat": {{
          "supportsStore": false,
          "supportsDeveloperRole": false,
          "supportsReasoningEffort": false,
          "maxTokensField": "max_tokens",
          "supportsStrictMode": false
        }}
      }}
    ]
  }}
}}"""

    if kind == "continue":
        return f"""{{
  "models": [
    {{
      "title": "WHUT {model}",
      "provider": "openai",
      "model": "{model}",
      "apiBase": "{base}",
      "apiKey": "{key}"
    }}
  ]
}}"""

    if kind == "codex":
        return f"""# 写入 ~/.codex/config.toml
model = "{model}"
model_provider = "whut"

[model_providers.whut]
name = "WHUT OpenWebUI"
base_url = "{base}"
env_key = "WHUT_API_KEY"
wire_api = "responses"
request_max_retries = 2
stream_max_retries = 1

# 然后在 shell 里导出 key（写进 ~/.zshrc 可长期生效）
#   export WHUT_API_KEY={key}"""

    if kind == "claude-code":
        settings_json = json.dumps(
            {
                "env": {
                    "ANTHROPIC_BASE_URL": host,
                    "ANTHROPIC_AUTH_TOKEN": key,
                    "ANTHROPIC_MODEL": model,
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "131072",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                }
            },
            indent=2,
            ensure_ascii=False,
        )
        return f"""# Claude Code 走 Anthropic 协议（本服务提供 /v1/messages）
# 方式一：临时覆盖（不动已有的 ~/.claude/settings.json）
export ANTHROPIC_BASE_URL="{host}"
export ANTHROPIC_AUTH_TOKEN="{key}"
export ANTHROPIC_MODEL="{model}"
export ANTHROPIC_SMALL_FAST_MODEL="{model}"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=131072
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

claude -p "只回复两个字：成功"

# 方式二：用独立 settings（推荐，避免和已有配置冲突）
# 存成 /tmp/whut-claude.json，然后： claude --settings /tmp/whut-claude.json
{settings_json}

# 协议自检：
#   curl -s {host}/v1/messages -H "x-api-key: {key}" -H "anthropic-version: 2023-06-01" \\
#     -H 'content-type: application/json' \\
#     -d '{{"model":"{model}","max_tokens":64,"messages":[{{"role":"user","content":"hi"}}]}}'"""

    if kind == "curl":
        return f"""curl -s {base}/chat/completions \\
  -H "Authorization: Bearer {key}" \\
  -H "Content-Type: application/json" \\
  -d '{{"model":"{model}","messages":[{{"role":"user","content":"你好"}}],"max_tokens":512}}'"""

    return f'''from openai import OpenAI

client = OpenAI(base_url="{base}", api_key="{key}")

resp = client.chat.completions.create(
    model="{model}",
    messages=[{{"role": "user", "content": "你好"}}],
    max_tokens=512,
)
print(resp.choices[0].message.content)
'''


__all__ = ["CONFIG_TEMPLATES", "attach", "error_payload"]
