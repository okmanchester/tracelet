"""Bounded synchronous retrieval work. Cancelling a waiter never frees a running slot."""

import asyncio
import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalContext:
    version: str | None
    deadline: float
    operation_id: str = ""
    trace_id: str = ""


current = contextvars.ContextVar("retrieval_context", default=None)


@contextmanager
def scope(version, *, operation_id="", trace_id="", deadline=None):
    token = current.set(RetrievalContext(version, deadline or time.monotonic() + settings.conversation_run_seconds,
                                         operation_id, trace_id))
    try:
        yield
    finally:
        current.reset(token)


def version():
    context = current.get()
    if context is None or context.version is None:
        raise RetrievalError("knowledge_version_required")
    return context.version


def remaining(deadline=None):
    context = current.get()
    limit = deadline if deadline is not None else context.deadline if context else None
    value = settings.tool_timeout_seconds if limit is None else min(settings.tool_timeout_seconds, limit - time.monotonic())
    if value <= 0:
        raise RetrievalError("retrieval_deadline")
    return value


class RetrievalPool:
    def __init__(self, workers, capacity):
        if workers < 1 or capacity < 1:
            raise ValueError("检索容量必须为正整数")
        self.queue = asyncio.Queue(maxsize=capacity)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="retrieval")
        self.closed = False
        self.active = 0
        self.counts = {"overloaded": 0, "expired": 0, "abandoned": 0, "discarded": 0}
        self.runners = [asyncio.create_task(self._worker()) for _ in range(workers)]

    async def _worker(self):
        while True:
            fn, context, promise, deadline, queued, phase = await self.queue.get()
            started = time.monotonic()
            try:
                if promise.cancelled() or started >= deadline:
                    self.counts["expired"] += 1
                    if not promise.done():
                        promise.set_exception(RetrievalError("retrieval_deadline"))
                    continue
                self.active += 1
                try:
                    result = await asyncio.get_running_loop().run_in_executor(self.executor, context.run, fn)
                    if not promise.done():
                        if time.monotonic() >= deadline:
                            self.counts["discarded"] += 1
                            promise.set_exception(RetrievalError("retrieval_deadline"))
                        else:
                            promise.set_result(result)
                    else:
                        self.counts["discarded"] += 1
                except Exception as exc:  # noqa: BLE001 -- 向原等待者传递错误；调度循环必须继续回收容量。
                    if not promise.done():
                        promise.set_exception(exc)
                finally:
                    self.active -= 1
                    ctx = context.get(current)
                    logger.info("retrieval phase=%s operation=%s trace=%s knowledge_version=%s queue_ms=%d run_ms=%d active=%d waiting=%d",
                                phase, "op_" + ctx.operation_id if ctx and ctx.operation_id else "", ctx.trace_id if ctx else "",
                                "kv_" + ctx.version if ctx and ctx.version else "", int((started-queued)*1000),
                                int((time.monotonic()-started)*1000), self.active, self.queue.qsize())
            finally:
                self.queue.task_done()

    async def run(self, fn, *, deadline=None, phase="retrieval"):
        context = current.get()
        deadline = deadline or (context.deadline if context else time.monotonic() + settings.conversation_run_seconds)
        if self.closed:
            raise RetrievalError("retrieval_closed")
        if time.monotonic() >= deadline:
            raise RetrievalError("retrieval_deadline")
        promise = asyncio.get_running_loop().create_future()
        try:
            self.queue.put_nowait((fn, contextvars.copy_context(), promise, deadline, time.monotonic(), phase))
        except asyncio.QueueFull:
            self.counts["overloaded"] += 1
            raise RetrievalError("retrieval_overloaded") from None
        try:
            return await asyncio.wait_for(asyncio.shield(promise), max(0, deadline-time.monotonic()))
        except (asyncio.CancelledError, TimeoutError):
            self.counts["abandoned"] += 1
            promise.cancel()
            raise

    async def close(self):
        self.closed = True
        while not self.queue.empty():
            _, _, promise, _, _, _ = self.queue.get_nowait()
            if not promise.done():
                promise.set_exception(RetrievalError("retrieval_closed"))
            self.queue.task_done()
        # Do not cancel dispatchers while their synchronous call is still alive.
        await self.queue.join()
        for task in self.runners:
            task.cancel()
        await asyncio.gather(*self.runners, return_exceptions=True)
        self.executor.shutdown(wait=True, cancel_futures=True)


def pool():
    loop = asyncio.get_running_loop()
    value = getattr(loop, "_tracelet_retrieval_pool", None)
    if value is None:
        value = RetrievalPool(settings.retrieval_workers, settings.retrieval_queue_capacity)
        loop._tracelet_retrieval_pool = value
    return value


async def run(fn, *, deadline=None, phase="retrieval"):
    return await pool().run(fn, deadline=deadline, phase=phase)


async def close():
    loop = asyncio.get_running_loop()
    value = getattr(loop, "_tracelet_retrieval_pool", None)
    if value is not None:
        await value.close()
        del loop._tracelet_retrieval_pool
    pending = getattr(loop, "_tracelet_bm25_pending", {})
    if pending:
        await asyncio.gather(*list(pending.values()), return_exceptions=True)
    from app.rag import retriever, store
    retriever.reset_bm25()
    store.close_clients()
