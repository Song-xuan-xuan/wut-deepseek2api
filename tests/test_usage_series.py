"""时间序列补零 + 仪表盘 KPI 数据源（回归：单点/稀疏数据被渲染成空白）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.core.db import Database
from app.core.settings import UsageSettings
from app.modules.usage.store import UsageStore


def make_store():
    tmp = Path(tempfile.mkdtemp(prefix="whut-series-"))
    settings = UsageSettings(db_path=str(tmp / "usage.db"))
    store = UsageStore(Database(settings.db_path), settings)
    store.connect()
    return store


def test_series_zero_fills_days_and_hours():
    store = make_store()
    store.record(model="deepseek-v4-flash", status=200, ms=100, usage={"prompt_tokens": 10, "completion_tokens": 5})

    week = store.series("7d")
    assert week["bucket"] == "day"
    assert len(week["points"]) == 7
    assert sum(point["requests"] for point in week["points"]) == 1
    assert week["points"][-1]["requests"] == 1

    day = store.series("24h")
    assert day["bucket"] == "hour"
    assert len(day["points"]) == 24
    assert sum(point["requests"] for point in day["points"]) == 1
    # 每个桶都要有完整的字段，前端直接读不会 undefined
    for point in day["points"]:
        for field in ("requests", "errors", "streams", "tool_calls", "prompt_tokens", "completion_tokens"):
            assert field in point

    month = store.series("30d")
    assert len(month["points"]) == 30

    today = store.series("today")
    assert 1 <= len(today["points"]) <= 24
    assert today["points"][-1]["requests"] == 1
    store.close()


def test_series_all_range_covers_first_record():
    store = make_store()
    store.record(model="m", status=200, ms=1)
    points = store.series("all")["points"]
    assert 1 <= len(points) <= 365
    assert sum(point["requests"] for point in points) == 1
    store.close()


def test_empty_store_still_returns_buckets():
    store = make_store()
    assert len(store.series("7d")["points"]) == 7
    assert all(point["requests"] == 0 for point in store.series("7d")["points"])
    store.close()


@pytest.mark.anyio
async def test_system_info_carries_today_and_series():
    """仪表盘 KPI 与 24h 折线都从 /api/system/info 取，字段缺了面板就全 0。"""
    import httpx

    from app.core.settings import (
        AccessSettings,
        ListenSettings,
        Settings,
        TokenRefreshSettings,
        UpstreamSettings,
    )
    from app.main import create_app
    from tests.conftest import MockState, build_mock  # type: ignore[import-not-found]

    state = MockState()
    mock = build_mock(state)
    tmp = Path(tempfile.mkdtemp(prefix="whut-info-"))
    settings = Settings(
        listen=ListenSettings(),
        upstream=UpstreamSettings(base_url="http://mock", token="test-jwt", token_file="", timeout_ms=30_000, retries=0),
        token_refresh=TokenRefreshSettings(enabled=False),
        usage=UsageSettings(db_path=str(tmp / "usage.db")),
        access=AccessSettings(default_rpm_limit=0),
        config_path=str(tmp / "config.json"),
        client_api_key="sk-test",
        default_model="deepseek-v4-flash",
        keep_alive_ms=0,
        log_requests=False,
    )
    app = create_app(settings, transport=httpx.ASGITransport(app=mock))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"Authorization": "Bearer sk-test"}
    ) as client:
        await client.post(
                "/v1/chat/completions",
            json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]},
        )
        usage = (await client.get("/api/system/info")).json()["usage"]
        assert usage["today"]["requests"] == 1
        assert usage["today"]["prompt_tokens"] == 10
        assert len(usage["series_24h"]["points"]) == 24
        assert sum(point["requests"] for point in usage["series_24h"]["points"]) == 1
