"""真实独立 MySQL 的事务/竞争；模型使用可控替身，Redis 由显式隔离连接提供。"""

import asyncio
import copy
import json
import os
from argparse import Namespace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import effects, models, operations, ticket_service, turns
from app.api import chat, conversations, tickets, workbench
from app.db import Base, get_db
from app.deps import CurrentUser, get_current_user
from app.guardrail import desensitize
from app.memory import context
from app.tools import ticket_tools
from app.tools.registry import ToolContext


def draft(*, ticket=False, human=False):
    return {"answer": "已根据证据完成诊断。", "intent": "api_error", "entities": {"app_id": "app_safe"},
            "handoff_requested": human, "planned_ticket": {
                "title": "synthetic", "summary": "safe", "category": "API报错", "priority": "P2",
            } if ticket else None}


def done(response):
    assert response.status_code == 200, response.text
    lines = response.text.splitlines()
    for i, line in enumerate(lines):
        if line == "event: done":
            return json.loads(lines[i + 1][6:])
    pytest.fail("成功流没有 done")


@pytest.fixture
async def store(mysql_engine, monkeypatch):
    from sse_starlette.sse import AppStatus
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    Base.metadata.create_all(mysql_engine)
    engine = create_async_engine(mysql_engine.url.set(drivername="mysql+aiomysql"), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in (chat, turns, effects, context, ticket_tools):
        monkeypatch.setattr(module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(context, "get_redis", lambda: SimpleNamespace(get=AsyncMock(return_value=None)))
    user = CurrentUser("u_a", "synthetic", "synthetic", "customer_dev", "a")
    actor = {"user": user}
    conv_id = "conv_" + uuid4().hex
    async with factory() as db:
        db.add_all([models.Conversation(id=conv_id, tenant_id="a", user_id="u_a"),
                    models.Conversation(id="other_" + conv_id, tenant_id="a", user_id="u_a")])
        await db.commit()
    app = FastAPI()
    from app.main import unsafe_content_error
    app.add_exception_handler(desensitize.SensitiveDataError, unsafe_content_error)
    for module in (chat, conversations, tickets, workbench):
        app.include_router(module.router)

    async def dependency():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[get_current_user] = lambda: actor["user"]
    real_run = chat.supervisor.run
    run = AsyncMock(side_effect=lambda **kwargs: copy.deepcopy(draft()))
    monkeypatch.setattr(chat.supervisor, "run", run)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(factory=factory, user=user, actor=actor, conv_id=conv_id,
                              client=client, run=run, engine=engine, real_run=real_run)
    await engine.dispose()


async def count(store, model):
    async with store.factory() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_first_turn_replay_conflict_identity_and_required_id(store):
    request_id = str(uuid4())
    body = {"request_id": request_id, "message": "safe"}
    first = done(await store.client.post("/api/chat", json=body))
    replay = done(await store.client.post("/api/chat", json=body))
    assert replay == first
    assert store.run.await_count == 1
    assert await count(store, models.Conversation) == 3
    assert await count(store, models.Message) == 2
    assert (await store.client.post("/api/chat", json={**body, "message": "changed"})).status_code == 409
    assert (await store.client.post("/api/chat", json={"message": "safe"})).status_code == 422
    assert (await store.client.post("/api/chat", json={**body, "request_id": "secret-text"})).status_code == 422
    queried = (await store.client.get(f"/api/operations/chat/{request_id}")).json()
    assert queried["operation_status"] == "succeeded"
    assert queried["conversation_id"] == first["conversation_id"]
    store.actor["user"] = CurrentUser("u_b", "synthetic", "synthetic", "customer_dev", "a")
    assert (await store.client.get(f"/api/operations/chat/{request_id}")).status_code == 404
    assert await count(store, models.Message) == 2


@pytest.mark.parametrize("action", ["takeover", "reply", "feedback"])
async def test_pending_busy_and_midflight_handoff_fences_all_writes(store, action):
    entered, release = asyncio.Event(), asyncio.Event()

    async def model(**kwargs):
        entered.set()
        await release.wait()
        return draft(ticket=True, human=True)

    store.run.side_effect = model
    request_id = str(uuid4())
    body = {"request_id": request_id, "message": "safe", "conversation_id": store.conv_id}
    first = asyncio.create_task(store.client.post("/api/chat", json=body))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await store.client.post("/api/chat", json=body)).status_code == 202
        assert (await store.client.post("/api/chat", json={**body, "request_id": str(uuid4())})).status_code == 409
        if action == "feedback":
            response = await store.client.post("/api/feedback", json={"request_id": str(uuid4()),
                "conversation_id": store.conv_id, "type": "need_human"})
        else:
            store.actor["user"] = CurrentUser("support", "support", "support", "support", "internal")
            response = await store.client.post(f"/api/workbench/conversations/{store.conv_id}/{action}",
                                               json={"content": "人工已回复"} if action == "reply" else {})
            store.actor["user"] = store.user
        assert response.status_code == 200, response.text
    finally:
        release.set()
    result = done(await first)
    assert result["need_human"] is True and result["discarded"] is True
    assert await count(store, models.Ticket) == (1 if action == "feedback" else 0)
    async with store.factory() as db:
        conv = await db.get(models.Conversation, store.conv_id)
        assert conv.collected_entities == {}
        rows = (await db.execute(select(models.Message))).scalars().all()
        assert len(rows) == (2 if action == "reply" else 1)
        assert not any(m.content == "已根据证据完成诊断。" for m in rows)


async def test_different_conversations_can_run_while_one_is_pending(store):
    async with store.factory() as db:
        first, _ = await operations.receive(db, store.user, "chat", uuid4(),
            {"message": "one", "conversation_id": store.conv_id})
    async with store.factory() as db:
        second, _ = await operations.receive(db, store.user, "chat", uuid4(),
            {"message": "two", "conversation_id": "other_" + store.conv_id})
    assert first.operation_id != second.operation_id
    assert await count(store, models.Message) == 2


async def test_expired_process_recovers_original_message_and_fences_old_owner(store):
    request_id = uuid4()
    payload = {"message": "safe", "conversation_id": store.conv_id}
    async with store.factory() as db:
        old, _ = await operations.receive(db, store.user, "chat", request_id, payload)
    async with store.factory() as db:
        past = await operations.db_now(db) - timedelta(seconds=1)
        await db.execute(update(models.Operation).values(lease_until=past))
        await db.execute(update(models.Conversation).where(models.Conversation.id == store.conv_id)
                         .values(lease_until=past))
        await db.commit()
    queried = (await store.client.get(f"/api/operations/chat/{request_id}")).json()
    assert queried["can_retry"] is True
    async with store.factory() as db:
        new, _ = await operations.receive(db, store.user, "chat", request_id, payload)
    assert new.generation == old.generation + 1
    async with store.factory() as db:
        rejected = await turns.finish(db, store.user, old, draft(ticket=True))
    assert rejected["operation_status"] == "processing"
    assert await count(store, models.Ticket) == 0
    async with store.factory() as db:
        result = await turns.finish(db, store.user, new, draft(ticket=True))
    assert result["operation_status"] == "succeeded"
    assert await count(store, models.Message) == 2
    assert await count(store, models.Ticket) == 1


async def test_commit_succeeded_but_reply_failed_returns_committed_ticket(store, monkeypatch):
    async with store.factory() as db:
        handle, _ = await operations.receive(db, store.user, "chat", uuid4(),
            {"message": "safe", "conversation_id": store.conv_id})
    async with store.factory() as db:
        commit = db.commit

        async def disconnected():
            await commit()
            raise ConnectionError("synthetic commit reply lost")

        monkeypatch.setattr(db, "commit", disconnected)
        with pytest.raises(ConnectionError):
            await turns.finish(db, store.user, handle, draft(ticket=True, human=True))
    recovered = await turns.recover_failure(store.user, handle, draft(ticket=True, human=True))
    assert recovered["operation_status"] == "succeeded"
    assert recovered["need_human"] is True
    assert await count(store, models.Ticket) == 1
    assert await count(store, models.Message) == 2


async def test_failed_ticket_retry_reuses_draft_and_does_not_enter_human_mode(store, monkeypatch):
    store.run.side_effect = lambda **kwargs: draft(ticket=True, human=True)
    create = ticket_service.create_in_session
    monkeypatch.setattr(ticket_service, "create_in_session", AsyncMock(side_effect=RuntimeError("failure")))
    body = {"request_id": str(uuid4()), "message": "safe", "conversation_id": store.conv_id}
    failed = await store.client.post("/api/chat", json=body)
    assert failed.status_code == 503, failed.text
    assert failed.json()["operation_status"] == "failed"
    async with store.factory() as db:
        assert (await db.get(models.Conversation, store.conv_id)).transferred_to_human is False
    assert await count(store, models.Ticket) == 0
    monkeypatch.setattr(ticket_service, "create_in_session", create)
    result = done(await store.client.post("/api/chat", json=body))
    assert result["need_human"] is True
    assert store.run.await_count == 1
    assert await count(store, models.Message) == 2
    assert await count(store, models.Ticket) == 1


async def test_diagnostic_ticket_keeps_ai_and_handoff_feedback_is_idempotent(store):
    store.run.side_effect = lambda **kwargs: draft(ticket=True)
    result = done(await store.client.post("/api/chat", json={"request_id": str(uuid4()),
        "message": "safe", "conversation_id": store.conv_id}))
    assert result["ticket_id"] and result["need_human"] is False
    body = {"request_id": str(uuid4()), "conversation_id": store.conv_id, "type": "need_human"}
    first = await store.client.post("/api/feedback", json=body)
    replay = await store.client.post("/api/feedback", json=body)
    assert replay.json() == first.json()
    new_request = await store.client.post("/api/feedback", json={**body, "request_id": str(uuid4())})
    assert first.json()["ticket_id"] == new_request.json()["ticket_id"]
    assert await count(store, models.Ticket) == 2
    assert await count(store, models.Feedback) == 2


async def test_standalone_tool_write_uses_trusted_id_and_reuses_ticket(store):
    ctx = ToolContext("a", user_id="u_a", role="customer_dev", operation_id=str(uuid4()))
    args = {"title": "safe", "summary": "safe", "conversation_id": store.conv_id}
    first = await ticket_tools.create_ticket(args, ctx)
    replay = await ticket_tools.create_ticket(args, ctx)
    assert first == replay
    assert await count(store, models.Ticket) == 1


@pytest.fixture
async def isolated_redis():
    from redis.asyncio import Redis
    url = os.environ.get("TRACELET_TEST_REDIS_URL")
    if not url:
        pytest.skip("需要显式独立 Redis 测试连接")
    redis = Redis.from_url(url, decode_responses=True)
    await redis.ping()
    yield redis
    await redis.aclose()


async def test_projection_fault_expiry_and_human_history_recover_from_sql(store, monkeypatch, isolated_redis):
    from app.conversation_service import append_message, locked_conversation
    async with store.factory() as db:
        conv = await locked_conversation(db, store.conv_id)
        for i in range(25):
            append_message(db, conv, "assistant" if i % 2 else "user", f"safe {i}", {"by": "human"})
        conv.collected_entities = {"app_id": "app_safe"}
        await db.commit()
    monkeypatch.setattr(context, "get_redis", lambda: isolated_redis)
    first = await context.load(store.conv_id)
    assert len(first["history"]) == 20 and first["history"][-1]["content"] == "safe 24"
    assert await context.rebuild(store.conv_id) == 1
    assert await context.load(store.conv_id) == first
    await isolated_redis.pexpire(f"mem:v2:{store.conv_id}", 1)
    await asyncio.sleep(0.02)
    assert await isolated_redis.get(f"mem:v2:{store.conv_id}") is None
    assert await context.load(store.conv_id) == first
    await isolated_redis.set(f"mem:v2:{store.conv_id}", "broken")
    assert await context.load(store.conv_id) == first
    await isolated_redis.delete(f"mem:v2:{store.conv_id}")
    assert await context.load(store.conv_id) == first
    async with store.factory() as db:
        handle, _ = await operations.receive(db, store.user, "chat", uuid4(),
            {"message": "new query", "conversation_id": store.conv_id})
    await context.rebuild(store.conv_id)
    loaded = await context.load(store.conv_id, handle.user_message_id)
    assert loaded["history"] == first["history"]
    assert loaded["entities"] == {"app_id": "app_safe"}
    assert all(m["content"] != "new query" for m in loaded["history"])
    await isolated_redis.set(f"mem:v2:{store.conv_id}", json.dumps({"version": 100}))
    assert await context.rebuild(store.conv_id) == 0
    assert (await context.load(store.conv_id))["history"][-1]["content"] == "new query"


async def test_effect_cost_trace_consumption_is_atomic_and_replay_safe(store):
    async with store.factory() as db:
        effects.enqueue(db, "effect_unique", store.conv_id, "a", {
            "cost": {"total_tokens": 17}, "trace": {"trace_id": "trace_safe", "steps": [{
                "agent_name": "probe", "step_order": 1, "input_summary": "safe", "output_summary": "safe",
                "status": "ok", "duration_ms": 1, "token_usage": 17, "hit_docs": [], "error_message": None,
            }]}})
        await db.commit()
    for _ in range(2):
        claimed = await effects.claim()
        assert claimed is not None
        await asyncio.gather(effects.consume(claimed), effects.consume(claimed))
    assert await count(store, models.TokenUsage) == 1
    assert await count(store, models.AgentTrace) == 1


async def test_effect_failure_survives_restart_and_manual_retry(store, monkeypatch):
    from scripts import effect_tasks
    monkeypatch.setattr(effect_tasks, "AsyncSessionLocal", store.factory)
    monkeypatch.setattr(context, "rebuild", AsyncMock(side_effect=RuntimeError("secret_key=synthetic")))
    async with store.factory() as db:
        effects.enqueue(db, "failed_effect", store.conv_id, "a")
        await db.commit()
    task_id = None
    for _ in range(10):
        claimed = await effects.claim()
        task_id = claimed[0]
        await effects.consume(claimed)
        async with store.factory() as db:
            await db.execute(update(models.EffectTask).values(
                available_at=await operations.db_now(db) - timedelta(seconds=1)))
            await db.commit()
    async with store.factory() as db:
        task = await db.get(models.EffectTask, task_id)
        assert task.status == "failed" and task.attempts == 10
        assert task.error == "附属写入失败"
    await effect_tasks.run(Namespace(command="retry", task_id=task_id))
    monkeypatch.setattr(context, "rebuild", AsyncMock(return_value=1))
    assert await effects.drain_once() is True
    async with store.factory() as db:
        assert (await db.get(models.EffectTask, task_id)).status == "done"


async def test_lost_task_owner_cannot_duplicate_cost_after_reclaim(store):
    async with store.factory() as db:
        effects.enqueue(db, "lost_effect", store.conv_id, "a", {"cost": {"total_tokens": 23}})
        await db.commit()
    original = await effects.claim()
    assert original[2] == "cost"
    async with store.factory() as db:
        await db.execute(update(models.EffectTask).where(models.EffectTask.id == original[0]).values(
            lease_until=await operations.db_now(db) - timedelta(seconds=1)))
        await db.commit()
    reclaimed = await effects.claim()
    assert reclaimed[0] == original[0] and reclaimed[1] != original[1]
    await effects.consume(original)
    assert await count(store, models.TokenUsage) == 0
    await effects.consume(reclaimed)
    assert await count(store, models.TokenUsage) == 1


async def test_semantic_effect_replay_is_atomic_in_real_redis(store, monkeypatch, isolated_redis):
    from app.cache import semantic_cache
    monkeypatch.setattr(semantic_cache, "get_redis", lambda: isolated_redis)
    monkeypatch.setattr(semantic_cache, "get_embedding_provider", lambda: SimpleNamespace(fingerprint="test"))
    tenant = "tenant_" + uuid4().hex
    task_id = "effect_" + uuid4().hex
    result = {"answer": "safe", "intent": "doc_qa", "citations": []}
    await asyncio.gather(*[semantic_cache.put(tenant, "safe", result, [1.0, 0.0], operation_id=task_id)
                           for _ in range(2)])
    assert await isolated_redis.llen(semantic_cache.key_for(tenant)) == 1


@pytest.mark.parametrize("failure", ["timeout", "pipeline"])
async def test_fallback_preserves_persisted_entities(store, monkeypatch, failure):
    async with store.factory() as db:
        conv = await db.get(models.Conversation, store.conv_id)
        conv.collected_entities = {"app_id": "app_old"}
        await db.commit()
    if failure == "timeout":
        async def never_complete(**kwargs):
            await asyncio.Event().wait()
        store.run.side_effect = never_complete
        monkeypatch.setattr(chat.settings, "conversation_run_seconds", 0.01)
    else:
        store.run.side_effect = store.real_run
        monkeypatch.setattr(chat.supervisor, "build_graph", lambda *args: SimpleNamespace(
            ainvoke=AsyncMock(side_effect=RuntimeError("synthetic pipeline failure"))))
    result = done(await store.client.post("/api/chat", json={"request_id": str(uuid4()),
        "message": "safe", "conversation_id": store.conv_id}))
    assert result["ticket_status"] == "created" and result["need_human"] is True
    async with store.factory() as db:
        assert (await db.get(models.Conversation, store.conv_id)).collected_entities == {"app_id": "app_old"}
    assert (await context.load(store.conv_id))["entities"] == {"app_id": "app_old"}


@pytest.mark.parametrize("source", ["model_output", "ticket_content", "recovery"])
async def test_security_failure_reaches_production_api_handler(store, monkeypatch, source):
    sanitize = desensitize.desensitize_text

    def failing_sanitizer(text):
        if text == "synthetic_unsafe_content":
            raise desensitize.SensitiveDataError()
        return sanitize(text)

    monkeypatch.setattr(desensitize, "desensitize_text", failing_sanitizer)
    if source == "model_output":
        store.run.side_effect = store.real_run
        monkeypatch.setattr(chat.supervisor, "build_graph", lambda *args: SimpleNamespace(
            ainvoke=AsyncMock(return_value={"final_answer": "synthetic_unsafe_content"})))
        url, body = "/api/chat", {"message": "safe"}
    else:
        if source == "ticket_content":
            from app.conversation_service import append_message, locked_conversation
            async with store.factory() as db:
                conv = await locked_conversation(db, store.conv_id)
                # 历史事实读取时安全处理失败，不能被反馈 API 降级为可重试建单失败。
                message = append_message(db, conv, "user", "safe")
                await db.flush()
                await db.execute(update(models.Message).where(models.Message.id == message.id)
                                 .values(content="synthetic_unsafe_content"))
                await db.commit()
        else:
            monkeypatch.setattr(ticket_service, "create_in_session", AsyncMock(side_effect=RuntimeError("failure")))
            monkeypatch.setattr(turns, "recover_failure", AsyncMock(side_effect=desensitize.SensitiveDataError()))
        url, body = "/api/feedback", {"type": "need_human"}
    response = await store.client.post(url, json={**body, "request_id": str(uuid4()),
                                                "conversation_id": store.conv_id})
    assert response.status_code == 500, response.text
    assert response.json() == {"detail": "内容安全处理失败，请稍后重试"}
    assert await count(store, models.Ticket) == 0
    assert await count(store, models.Feedback) == 0
    assert await count(store, models.EffectTask) == 0
    async with store.factory() as db:
        assert (await db.get(models.Conversation, store.conv_id)).transferred_to_human is False
        assert not (await db.execute(select(models.Message).where(models.Message.role == "assistant"))).first()


@pytest.mark.parametrize("surface,ticket", [("answer", True), ("answer", False), ("card", False), ("citations", False)])
async def test_model_action_claim_is_replaced_by_committed_state(store, surface, ticket):
    generated = draft(ticket=ticket)
    claim = "已为您创建工单，并已转人工。"
    generated[surface] = claim if surface == "answer" else {"text": claim}
    store.run.side_effect = lambda **kwargs: generated
    result = done(await store.client.post("/api/chat", json={"request_id": str(uuid4()),
        "message": "safe", "conversation_id": store.conv_id}))
    assert "已转人工" not in json.dumps(result, ensure_ascii=False)
    assert result["need_human"] is False and result["handoff_status"] == "not_requested"
    assert result["ticket_status"] == ("created" if ticket else "none")
    assert result["card"] is None and result["citations"] == []
    assert await count(store, models.Ticket) == int(ticket)
    async with store.factory() as db:
        assistant = (await db.execute(select(models.Message).where(models.Message.role == "assistant"))).scalar_one()
        assert assistant.content == result["answer"] and claim not in assistant.content
        assert (await db.get(models.Conversation, store.conv_id)).transferred_to_human is False


@pytest.mark.parametrize("status", ["closed", "resolved"])
async def test_human_ack_and_closed_handoff_ticket_replacement(store, status):
    body = {"conversation_id": store.conv_id, "type": "need_human"}
    first = (await store.client.post("/api/feedback", json={**body, "request_id": str(uuid4())})).json()
    ack = done(await store.client.post("/api/chat", json={"request_id": str(uuid4()),
        "message": "progress", "conversation_id": store.conv_id}))
    assert ack["ticket_id"] == first["ticket_id"] and ack["ticket_status"] == "created"
    store.actor["user"] = CurrentUser("support", "support", "support", "support", "internal")
    closed = await store.client.post(f"/api/workbench/tickets/{first['ticket_id']}", json={"status": status})
    assert closed.status_code == 200, closed.text
    store.actor["user"] = store.user
    request = {**body, "request_id": str(uuid4())}
    new = (await store.client.post("/api/feedback", json=request)).json()
    assert new["ticket_id"] != first["ticket_id"] and new["ticket_status"] == "created"
    assert (await store.client.post("/api/feedback", json=request)).json() == new
    assert await count(store, models.Ticket) == 2
    async with store.factory() as db:
        assert (await db.get(models.Conversation, store.conv_id)).handoff_ticket_id == new["ticket_id"]


async def test_no_hit_explanation_and_human_option_are_preserved(store):
    generated = draft()
    generated["answer"] = "现有文档没有命中，请补充问题或选择人工支持。"
    store.run.side_effect = lambda **kwargs: generated
    result = done(await store.client.post("/api/chat", json={"request_id": str(uuid4()),
        "message": "safe", "conversation_id": store.conv_id}))
    assert result["answer"] == generated["answer"]
    assert result["need_human"] is False and result["ticket_status"] == "none"
    assert await count(store, models.Ticket) == 0
