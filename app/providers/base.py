"""上游提供方抽象。

目前只有一个实现（学校 OpenWebUI），但接口按"可扩展"设计：
以后想接 DeepSeek 官方、校内 vLLM、或 OpenRouter，只要再写一个 Provider 即可，
proxy 模块完全不用改。

Provider 需要提供：
    chat(body)           → httpx.Response（流式时未读取的响应）
    list_models()        → 上游原始模型列表
    session()            → 当前凭证属于谁
    version()            → 上游版本信息（可选）
    signin(email, pwd)   → 新 token（可选，没有就没法自动续期）
    circuit()            → 熔断状态
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx


class ProviderError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@runtime_checkable
class Provider(Protocol):
    name: str
    base_url: str

    async def chat(self, body: dict[str, Any]) -> httpx.Response: ...

    async def list_models(self) -> Any: ...

    async def session(self) -> dict[str, Any]: ...

    async def version(self) -> dict[str, Any] | None: ...

    async def signin(self, email: str, password: str) -> str: ...

    def circuit(self) -> dict[str, Any]: ...

    def token_status(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...
