"""模型健康看门狗：上游反复报错的模型先藏起来，避免客户端选中它后一直失败。

规则（都在内存里，重启清零）：
- 同一模型在 `window` 秒内累计 `threshold` 次"上游限流/不可用"，隔离 `quarantine` 秒；
- 隔离期内 `/v1/models` 不再列出该模型（面板「系统信息」里能看到剩余时间）；
- 期间任何一次成功都会立刻解除隔离。
"""

from __future__ import annotations

import threading
import time
from typing import Any

# 视为"模型本身暂时不可用"的上游状态；400 里带 429 的先由调用方归一成 429
FAILURE_STATUSES = {429, 503}


class ModelGuard:
    def __init__(self, threshold: int = 2, window: float = 300.0, quarantine: float = 600.0):
        self.threshold = max(1, threshold)
        self.window = window
        self.quarantine = quarantine
        self._fails: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def record_failure(self, model: str, status: int) -> None:
        if not model or status not in FAILURE_STATUSES:
            return
        now = time.time()
        with self._lock:
            stamps = [t for t in self._fails.get(model, []) if now - t <= self.window]
            stamps.append(now)
            self._fails[model] = stamps
            if len(stamps) >= self.threshold:
                self._blocked_until[model] = now + self.quarantine

    def record_success(self, model: str) -> None:
        if not model:
            return
        with self._lock:
            self._fails.pop(model, None)
            self._blocked_until.pop(model, None)

    def remaining(self, model: str) -> float:
        with self._lock:
            until = self._blocked_until.get(model, 0.0)
        return max(0.0, until - time.time())

    def is_blocked(self, model: str) -> bool:
        return self.remaining(model) > 0

    def snapshot(self) -> dict[str, Any]:
        """给面板看：被隔离的模型 + 剩余秒数。"""
        now = time.time()
        with self._lock:
            blocked = {m: round(until - now, 1) for m, until in self._blocked_until.items() if until > now}
        return {"blocked": blocked, "threshold": self.threshold, "quarantine_seconds": self.quarantine}
