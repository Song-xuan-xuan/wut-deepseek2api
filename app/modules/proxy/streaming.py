"""SSE 透传：字节级转发 + 心跳 + 收尾兜底 + 顺带抽 usage。

设计要求：
- **不改变透传字节**（agent 拿到的就是上游原始流）；
- 上游不发 `[DONE]` 时补一个，否则客户端会一直挂着等；
- 静默超过 keep_alive_ms 发一行 SSE 注释保活；
- 上游断流时发一条 error 事件再收尾；
- 只在出现 `usage` / `tool_calls` / `finish_reason` 关键字时才尝试解析 JSON，
  用于统计，开销可以忽略。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from ..shared import error_payload

KEEPALIVE = b": keepalive\n\n"
DONE = b"data: [DONE]\n\n"


def scan_lines(pending: bytes, observe: Callable[[dict[str, Any]], None]) -> bytes:
    """从字节流里增量抽出关键事件，返回未处理的残留。"""
    while True:
        index = pending.find(b"\n")
        if index < 0:
            return pending[-4096:] if len(pending) > 65536 else pending
        line, pending = pending[:index], pending[index + 1 :]
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == b"[DONE]":
            continue
        if not (b'"usage"' in payload or b'"tool_calls"' in payload or b'"finish_reason"' in payload):
            continue
        try:
            observe(json.loads(payload))
        except (ValueError, TypeError):
            continue


async def sse_stream(
    resp: httpx.Response,
    keep_alive_ms: int,
    observe: Callable[[dict[str, Any]], None] | None = None,
    on_finish: Callable[[bool], None] | None = None,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=256)

    async def producer() -> None:
        try:
            async for chunk in resp.aiter_raw():
                await queue.put(chunk)
        except Exception as exc:  # noqa: BLE001 - 任何上游异常都要转成客户端可见事件
            await queue.put(exc)
        finally:
            await queue.put(None)

    task = asyncio.create_task(producer())
    tail = b""
    pending = b""
    saw_done = False
    try:
        while True:
            timeout = keep_alive_ms / 1000 if keep_alive_ms else None
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                yield KEEPALIVE
                continue

            if item is None:
                break
            if isinstance(item, Exception):
                payload = json.dumps(error_payload(f"上游流中断：{item}", 502), ensure_ascii=False)
                yield f"data: {payload}\n\n".encode()
                break

            yield item
            tail = (tail + item)[-80:]
            if b"[DONE]" in tail:
                saw_done = True
            if observe is not None:
                pending = scan_lines(pending + item, observe)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        with contextlib.suppress(Exception):
            await resp.aclose()

    if not saw_done:
        yield DONE
    if on_finish is not None:
        on_finish(saw_done)
