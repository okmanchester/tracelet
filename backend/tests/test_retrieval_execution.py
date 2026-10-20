"""Deterministic scheduling barriers exercise real threads, not sleep timing assumptions."""

import asyncio
import contextlib
import threading
import time

import pytest

from app.rag import execution, retriever


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_running_cancel_keeps_capacity_queued_cancel_skips_and_overflow_rejects():
    pool = execution.RetrievalPool(1, 1)
    entered, release, queued = threading.Event(), threading.Event(), threading.Event()
    def blocking():
        entered.set()
        assert release.wait(3)
    first = asyncio.create_task(pool.run(blocking))
    second = None
    try:
        await until(entered.is_set)
        import httpx

        from app.main import app
        async with asyncio.timeout(1), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
        assert pool.active == 1  # The real blocking call is still held while HTTP is schedulable.
        second = asyncio.create_task(pool.run(queued.set))
        await until(lambda: pool.queue.qsize() == 1)
        with pytest.raises(execution.RetrievalError, match="overloaded"):
            await pool.run(lambda: None)
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first
        assert pool.active == 1 and not queued.is_set()
        second.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await second
        release.set()
        await pool.queue.join()
        assert not queued.is_set() and pool.active == 0
    finally:
        release.set()
        await pool.close()
    with pytest.raises(execution.RetrievalError, match="closed"):
        await pool.run(lambda: None)


async def test_deadline_and_close_reject_queued_work_without_releasing_running_slot():
    pool = execution.RetrievalPool(1, 2)
    entered, release, unwanted = threading.Event(), threading.Event(), threading.Event()
    def blocking():
        entered.set()
        release.wait(3)
    first = asyncio.create_task(pool.run(blocking))
    try:
        await until(entered.is_set)
        with pytest.raises(TimeoutError):
            await pool.run(unwanted.set, deadline=time.monotonic()+0.02)
        assert pool.active == 1 and not unwanted.is_set()
        queued = asyncio.create_task(pool.run(unwanted.set))
        await until(lambda: pool.queue.qsize() == 2)
        closing = asyncio.create_task(pool.close())
        with pytest.raises(execution.RetrievalError, match="closed"):
            await queued
        assert not closing.done() and pool.active == 1
        release.set()
        await first
        await closing
        assert not unwanted.is_set()
    finally:
        release.set()
        if not pool.closed:
            await pool.close()


async def test_singleflight_survives_one_waiter_cancellation_and_lru_rebuild(monkeypatch):
    retriever.reset_bm25()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def build(version):
        calls.append(version)
        entered.set()
        assert release.wait(3)
        return version, object()
    monkeypatch.setattr(retriever, "_build_index", build)
    monkeypatch.setattr(retriever.settings, "retrieval_bm25_cache_versions", 1)
    one = asyncio.create_task(retriever.get_index("one"))
    two = asyncio.create_task(retriever.get_index("one"))
    try:
        await until(entered.is_set)
        one.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await one
        release.set()
        original = await two
        assert calls == ["one"]
        await retriever.get_index("two")
        rebuilt = await retriever.get_index("one")
        assert calls == ["one", "two", "one"] and original is not rebuilt
    finally:
        release.set()
