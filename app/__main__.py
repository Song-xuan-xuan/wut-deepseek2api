"""python -m app 的入口。"""

from __future__ import annotations

import uvicorn

from .core.settings import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    host, port = settings.listen.host, settings.listen.port

    print(f"whut-2api  →  http://{host}:{port}/v1")
    print(f"  面板     http://{host}:{port}/dashboard")
    print(f"  上游     {settings.upstream.base_url}")
    print(f"  凭证     {settings.upstream.token_file}")
    print(f"  用量库   {settings.usage.db_path}")
    print(f"  本地 key {settings.client_api_key or '(未设置，任何人可访问)'}")
    if settings.token_refresh.email:
        print(f"  学号     {settings.token_refresh.email}（自动续期{'开' if settings.token_refresh.enabled else '关'}）")
    else:
        print("  学号     未配置 → 打开面板点「一键认证」")
    print(f"\n自检: curl -s http://{host}:{port}/health\n", flush=True)

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
