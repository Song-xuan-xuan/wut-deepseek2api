"""Provider 注册表。

现在只有 openwebui 一个实现；以后要接官方 API / 校内 vLLM，
在这里加一个分支 + 一个 Provider 类即可，其余模块不用改。
"""

from __future__ import annotations

import httpx

from ..core.settings import Settings
from .base import Provider, ProviderError
from .openwebui import OpenWebUIProvider

__all__ = ["OpenWebUIProvider", "Provider", "ProviderError", "build_provider"]


def build_provider(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> Provider:
    # 未来：settings.upstream.kind == "deepseek" 时返回 DeepSeekOfficialProvider
    return OpenWebUIProvider(settings.upstream, transport=transport)
