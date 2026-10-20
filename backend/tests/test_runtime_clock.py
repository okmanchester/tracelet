"""运行时间与历史窗口：SQLite 控制流和独立 MySQL 上运行相同查询断言。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import clock, models, ticket_service
from app.agents import api_diagnostic
from app.llm.base import ChatResult
from app.tools import apikey, logs, ticket_tools
from app.tools.registry import ToolContext

NOW = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
CTX = ToolContext("a", user_id="u_a", role="customer_dev")


@pytest.fixture(params=["sqlite", "mysql"])
def time_store(request, monkeypatch):
    if request.param == "mysql":
        engine = request.getfixturevalue("mysql_engine")
    else:
        engine = create_engine("sqlite:///:memory:")
    # 无 Message 微秒类型或其他业务依赖，只创建本测试使用的表。
    for model in (models.Plan, models.Tenant, models.App, models.Conversation, models.ApiKey,
                  models.ApiCallLog, models.Ticket, models.Feedback):
        model.__table__.create(engine)
    with Session(engine) as session:
        session.add(models.Tenant(id="a", name="synthetic"))
        session.flush()
        session.add(models.App(id="app_a", tenant_id="a", name="synthetic"))
        session.add(models.Conversation(id="c_a", tenant_id="a", user_id="u_a"))
        session.commit()

        class AsyncDB:
            async def execute(self, statement):
                return session.execute(statement)

            def add(self, row):
                # SQLite 不模拟 MySQL BIGINT 自增；该测试只验证反馈建单的 UTC 日期。
                if isinstance(row, models.Feedback) and engine.dialect.name == "sqlite":
                    row.id = 1
                session.add(row)

            async def commit(self):
                session.commit()

        db = AsyncDB()

        @asynccontextmanager
        async def session_factory():
            yield db

        for module in (logs, apikey, ticket_tools):
            monkeypatch.setattr(module, "AsyncSessionLocal", session_factory)
        monkeypatch.setattr(clock, "now_utc", lambda: NOW)
        yield session, db
    if request.param == "sqlite":
        engine.dispose()


def add_log(session, identifier, when, *, status=200, tenant="a", endpoint="/target"):
    session.add(models.ApiCallLog(
        request_id=identifier, tenant_id=tenant, app_id="app_a", endpoint=endpoint,
        http_status=status, created_at=clock.storage_utc(when), latency_ms=1,
    ))


async def test_realtime_window_uses_now_and_both_bounds(time_store):
    session, _ = time_store
    for identifier, when, status in (
        ("old429", NOW - timedelta(days=10), 429),
        ("before", NOW - timedelta(minutes=60, seconds=1), 200),
        ("start", NOW - timedelta(minutes=60), 200),
        ("inside", NOW - timedelta(seconds=1), 429),
        ("end", NOW, 200),
        ("future", NOW + timedelta(seconds=1), 429),
    ):
        add_log(session, identifier, when, status=status)
    add_log(session, "other_tenant", NOW, tenant="b")
    add_log(session, "other_endpoint", NOW, endpoint="/other")
    session.commit()
    result = await logs.query_recent_call_stats({"endpoint": "/target"}, CTX)
    assert result["found"] is True
    assert result["window_mode"] == "realtime"
    assert result["window_minutes"] == 60
    assert result["window_end"] == NOW.isoformat()
    assert result["window_start"] == (NOW - timedelta(minutes=60)).isoformat()
    assert result["total"] == 2
    assert result["by_status"] == {200: 1, 429: 1}
    assert result["rate_limited_ratio"] == 0.5


async def test_historical_window_uses_explicit_event_and_excludes_later_data(time_store):
    session, _ = time_store
    event = NOW - timedelta(days=30)
    for identifier, when, status in (
        ("before", event - timedelta(minutes=30, seconds=1), 200),
        ("start", event - timedelta(minutes=30), 200),
        ("inside", event - timedelta(seconds=1), 200),
        ("anchor", event, 429),
        ("later429", event + timedelta(seconds=1), 429),
        ("now", NOW, 200),
    ):
        add_log(session, identifier, when, status=status)
    session.commit()
    result = await logs.query_recent_call_stats(
        {"endpoint": "/target", "minutes": 30, "request_id": "anchor"}, CTX,
    )
    assert result["window_mode"] == "historical"
    assert result["window_end"] == event.isoformat()
    assert result["total"] == 2
    assert result["rate_limited_count"] == 1


@pytest.mark.parametrize("identifier", ["missing", "foreign", "wrong_endpoint", ""])
async def test_invalid_history_anchor_does_not_fall_back_or_leak(time_store, identifier):
    session, _ = time_store
    add_log(session, "foreign", NOW, tenant="b")
    add_log(session, "wrong_endpoint", NOW, endpoint="/other")
    add_log(session, "visible", NOW)
    session.commit()
    result = await logs.query_recent_call_stats(
        {"endpoint": "/target", "request_id": identifier}, CTX,
    )
    assert result == {"found": False, "reason": "未找到对应调用事件或无权访问"}


async def test_empty_realtime_window_still_reports_actual_bounds(time_store):
    result = await logs.query_recent_call_stats({}, CTX)
    assert result["found"] is True
    assert result["total"] == result["rate_limited_count"] == 0
    assert result["by_status"] == {}
    assert result["rate_limited_ratio"] == 0.0
    assert result["window_end"] == NOW.isoformat()


@pytest.mark.parametrize("minutes", [0, -1, 0.5, "60", True, 10**100])
async def test_invalid_window_rejected_before_aggregate(time_store, minutes):
    with pytest.raises(ValueError, match="minutes"):
        await logs.query_recent_call_stats({"minutes": minutes}, CTX)


async def test_key_expiry_at_boundary_and_unbounded_keys(time_store):
    session, _ = time_store
    for identifier, expires in (
        ("past", NOW - timedelta(seconds=1)), ("exact", NOW),
        ("future", NOW + timedelta(seconds=1)), ("forever", None),
    ):
        session.add(models.ApiKey(
            id=identifier, app_id="app_a", tenant_id="a", key_masked=identifier,
            status="ACTIVE", expire_at=clock.storage_utc(expires) if expires else None,
        ))
    session.add(models.ApiKey(id="foreign", app_id="app_a", tenant_id="b",
                              key_masked="foreign", status="ACTIVE", expire_at=None))
    session.commit()
    result = await apikey.query_apikey_status({"app_id": "app_a"}, CTX)
    keys = {row["api_key_masked"]: row for row in result["keys"]}
    assert {key: row["expired"] for key, row in keys.items()} == {
        "past": True, "exact": True, "future": False, "forever": False,
    }
    assert keys["exact"]["expire_at"] == NOW.isoformat()


async def test_shared_ticket_service_uses_runtime_utc_date(time_store):
    session, db = time_store
    conv = session.get(models.Conversation, "c_a")
    result = await ticket_service.create_in_session(db,
        {"title": "safe", "category": "test", "summary": "safe"}, CTX, "one", conv)
    second = await ticket_service.create_in_session(db,
        {"title": "safe", "category": "test", "summary": "safe"}, CTX, "two", conv)
    await db.commit()
    assert result.ticket_id.startswith("tk_20261020_")
    assert second.ticket_id.startswith("tk_20261020_")
    assert result.ticket_id != second.ticket_id
    assert set(session.scalars(select(models.Ticket.ticket_id))) == {result.ticket_id, second.ticket_id}


@pytest.mark.parametrize("stamp", [
    datetime(2026, 10, 19, 23, 59, 59),
    datetime(2026, 10, 20, tzinfo=UTC),
    datetime(2026, 10, 20, 8, tzinfo=timezone(timedelta(hours=8))),
    datetime(2026, 10, 19, 17, tzinfo=timezone(timedelta(hours=-7))),
])
def test_clock_normalizes_storage_timezone(stamp):
    normalized = clock.as_utc(stamp)
    expected = NOW if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)
    assert normalized == expected
    assert normalized.utcoffset() == timedelta(0)
    assert clock.storage_utc(stamp).tzinfo is None
    assert clock.as_utc(clock.storage_utc(stamp)) == normalized


def test_real_clock_is_aware_and_not_frozen():
    before = datetime.now(UTC)
    actual = clock.now_utc()
    after = datetime.now(UTC)
    assert before <= actual <= after
    assert actual.utcoffset() == timedelta(0)


@pytest.mark.parametrize("tick, expected", [
    (datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC), "tk_20261019_"),
    (datetime(2026, 10, 20, tzinfo=UTC), "tk_20261020_"),
])
def test_ticket_date_rolls_over_at_utc_midnight(monkeypatch, tick, expected):
    monkeypatch.setattr(clock, "now_utc", lambda: tick)
    assert ticket_service.new_ticket_id("synthetic").startswith(expected)


@pytest.mark.parametrize("found", [True, False])
async def test_diagnostic_passes_only_found_event_to_stats(monkeypatch, found):
    calls = []

    async def execute(name, args, ctx):
        calls.append((name, args))
        if name == "query_call_log":
            return {"ok": True, "data": {"found": found, "endpoint": "/target"}}
        return {"ok": True, "data": {"total": 0}}

    monkeypatch.setattr(api_diagnostic, "execute", execute)
    monkeypatch.setattr(api_diagnostic.doc_rag, "answer", AsyncMock(return_value=SimpleNamespace(
        answer="safe", citations=[], tokens=0,
    )))
    monkeypatch.setattr(api_diagnostic.client, "chat", AsyncMock(return_value=ChatResult(
        content='{"conclusion":"safe","evidence":[],"steps":[]}',
    )))
    result = await api_diagnostic.diagnose(
        "safe", {"request_id": "synthetic", "endpoint": "/target"}, CTX,
    )
    assert result.evidence["recent_stats"]["total"] == 0
    args = next(args for name, args in calls if name == "query_recent_call_stats")
    assert ("request_id" in args) is found
    if found:
        assert args["request_id"] == "synthetic"
