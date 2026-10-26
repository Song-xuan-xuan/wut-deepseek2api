"""统一的日志入口。

面板/脚本都调这里，方便以后换成 logging 或加文件轮转。
"""

from __future__ import annotations

import time


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def noop(_message: str) -> None:
    return None
