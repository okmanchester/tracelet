"""访问矩阵及敏感数据验证只操作 fixture 创建的随机 MySQL 库。"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from app import models
from app.api import chat, conversations, tickets, workbench
from app.db import Base, get_db
from app.deps import CurrentUser, get_current_user
from app.llm.base import ChatResult
from app.observability import trace as trace_module
from app.tools import registry as tool_registry
from app.tools import ticket_tools
from app.tools.registry import ToolContext
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session


@pytest.fixture
def pilot_store(mysql_engine, monkeypatch):
    from sse_starlette.sse import AppStatus

    # sse-starlette 2.x 的退出事件不能跨 pytest 的独立事件循环复用。
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    Base.metadata.create_all(mysql_engine)
    with Session(mysql_engine) as session:
        session.add_all(models.Tenant(id=tenant, name=tenant) for tenant in ("a", "b", "internal"))
        session.flush()
        users = {}
        for tenant in ("a", "b"):
            for suffix, role in (("1", "customer_dev"), ("2", "customer_dev"), ("a", "customer_admin")):
                uid = f"u_{tenant}{suffix}"
                users[uid] = CurrentUser(uid, uid, uid, role, tenant)
        for role in ("support", "admin"):
            users[role] = CurrentUser(role, role, role, role, "internal")
        for uid, user in users.items():
            session.add(models.User(id=uid, username=uid, tenant_id=user.tenant_id,
                                    role=user.role, display_name=uid, password_hash="synthetic"))
            session.add(models.Conversation(id=f"c_{uid}", tenant_id=user.tenant_id, user_id=uid))
        session.flush()
        for uid, user in users.items():
            session.add(models.Message(id=f"m_{uid}", conversation_id=f"c_{uid}",
                                       role="user", content="safe request"))
            session.add(models.Ticket(ticket_id=f"t_{uid}", tenant_id=user.tenant_id, user_id=uid,
                                      conversation_id=f"c_{uid}", title="safe", category="test", priority="P2"))
        for tid, owner, conv in (
            ("t_unlinked", "u_a1", None), ("t_conflict", "u_a1", "c_u_a2"),
            ("t_cross", "u_a1", "c_u_b1"), ("t_orphan", "u_a1", "c_missing"),
            ("t_missing_owner", "", None),
        ):
            session.add(models.Ticket(ticket_id=tid, tenant_id="a", user_id=owner,
                                      conversation_id=conv, title="legacy", category="test", priority="P2"))
        session.commit()

        class AsyncDB:
            async def execute(self, statement):
                return session.execute(statement)

            async def scalar(self, statement):
                return session.scalar(statement)

            def add(self, row):
                session.add(row)

            async def commit(self):
                session.commit()

        db = AsyncDB()

        @asynccontextmanager
        async def session_factory():
            yield db

        app = FastAPI()
        for router in (chat.router, conversations.router, tickets.router, workbench.router):
            app.include_router(router)
        actor = {"user": users["u_a1"]}
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: actor["user"]
        monkeypatch.setattr(ticket_tools, "AsyncSessionLocal", session_factory)

        def counts():
            return tuple(session.scalar(select(func.count()).select_from(model))
                         for model in (models.Conversation, models.Message, models.Feedback, models.Ticket))

        yield app, session, users, actor, counts, session_factory


def expected_reads(users, uid):
    if uid in {"support", "admin"}:
        return set(users)
    if uid.endswith("a"):
        return {other for other, user in users.items() if user.tenant_id == users[uid].tenant_id}
    return {uid}


async def test_conversation_http_read_write_matrix(pilot_store, monkeypatch):
    app, session, users, actor, counts, _ = pilot_store
    run = AsyncMock(return_value={"answer": "ready", "intent": "chitchat"})
    monkeypatch.setattr(chat.supervisor, "run", run)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for uid, user in users.items():
            actor["user"] = user
            listed = (await client.get("/api/conversations")).json()["conversations"]
            assert {row["id"] for row in listed} == {f"c_{other}" for other in expected_reads(users, uid)}
            for owner in users:
                cid = f"c_{owner}"
                response = await client.get(f"/api/conversations/{cid}")
                assert response.status_code == (200 if owner in expected_reads(users, uid) else 403)
                if response.status_code == 200:
                    assert response.json()["conversation"]["can_write"] is (owner == uid)
                for path, body in (
                    (f"/api/conversations/{cid}/messages", {"content": "probe"}),
                    ("/api/chat", {"conversation_id": cid, "message": "probe"}),
                    ("/api/feedback", {"conversation_id": cid, "type": "resolved", "message_id": f"m_{owner}"}),
                ):
                    before, calls = counts(), run.await_count
                    response = await client.post(path, json=body)
                    assert response.status_code == (200 if owner == uid else 403), (uid, owner, path)
                    if owner != uid:
                        assert counts() == before
                        assert run.await_count == calls
        actor["user"] = users["u_a1"]
        for cid in ("c_missing", ""):
            before, calls = counts(), run.await_count
            response = await client.post("/api/chat", json={"conversation_id": cid, "message": "probe"})
            assert response.status_code == 404
            assert counts() == before
            assert run.await_count == calls
        for body in (
            {"conversation_id": "c_u_a1", "type": "bad_type"},
            {"conversation_id": "c_u_a1", "type": "resolved", "message_id": "m_u_a2"},
            {"conversation_id": "c_u_a1", "type": "resolved", "message_id": "missing"},
        ):
            before = counts()
            assert (await client.post("/api/feedback", json=body)).status_code == 422
            assert counts() == before
        session.add_all(models.Conversation(id=f"limit_{i}", tenant_id="a", user_id="u_a2") for i in range(55))
        session.commit()
        listed = (await client.get("/api/conversations")).json()["conversations"]
        assert {row["id"] for row in listed} == {"c_u_a1"}


async def test_ticket_http_and_tool_matrix_handles_legacy_ownership(pilot_store):
    app, session, users, actor, _, _ = pilot_store
    all_tickets = {row.ticket_id: row for row in session.scalars(select(models.Ticket))}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for uid, user in users.items():
            actor["user"] = user
            if uid in {"support", "admin"}:
                allowed = set(all_tickets)
            elif user.role == "customer_admin":
                allowed = {tid for tid, row in all_tickets.items() if row.tenant_id == user.tenant_id}
            else:
                allowed = {f"t_{uid}"} | ({"t_unlinked"} if uid == "u_a1" else set())
            listed = (await client.get("/api/tickets")).json()["tickets"]
            assert {row["ticket_id"] for row in listed} == allowed
            ctx = ToolContext(user.tenant_id, user_id=uid, role=user.role, is_internal=True)
            assert ctx.is_internal is (uid in {"support", "admin"})
            for tid in all_tickets:
                response = await client.get(f"/api/tickets/{tid}")
                assert response.status_code == (200 if tid in allowed else 403)
                result = await ticket_tools.query_ticket({"ticket_id": tid}, ctx)
                assert result["found"] is (tid in allowed)
            assert (await client.get("/api/tickets/missing")).status_code == 404
            assert await ticket_tools.query_ticket({"ticket_id": "missing"}, ctx) == {"found": False}
        actor["user"] = users["u_a1"]
        session.add_all(models.Ticket(ticket_id=f"limit_{i}", tenant_id="a", user_id="u_a2",
                                      title="other", category="test", priority="P2") for i in range(55))
        session.commit()
        listed = (await client.get("/api/tickets")).json()["tickets"]
        assert {row["ticket_id"] for row in listed} == {"t_u_a1", "t_unlinked"}
    assert await ticket_tools.query_ticket({"ticket_id": "t_u_a1"}, ToolContext("a", is_internal=True)) == {"found": False}


async def test_tool_creation_uses_trusted_identity_and_rejects_spoofing(pilot_store):
    _, session, _, _, counts, _ = pilot_store
    ctx = ToolContext("a", user_id="u_a1", role="customer_dev")
    for args in (
        {"user_id": "u_a2"}, {"tenant_id": "b"}, {"conversation_id": "c_u_a2"},
        {"conversation_id": "c_u_b1"}, {"conversation_id": "c_missing"},
    ):
        before = counts()
        with pytest.raises((ValueError, HTTPException)):
            await ticket_tools.create_ticket({"title": "probe", **args}, ctx)
        assert counts() == before
    created = await ticket_tools.create_ticket({"title": "sk-tiny", "conversation_id": "c_u_a1",
                                               "summary": "Bearer fake-token", "evidence": "contact@example.test"}, ctx)
    row = session.get(models.Ticket, created["ticket_id"])
    assert (row.tenant_id, row.user_id, row.conversation_id) == ("a", "u_a1", "c_u_a1")
    assert "sk-tiny" not in row.title
    assert "fake-token" not in row.summary
    assert "contact@example.test" not in row.evidence


async def test_workbench_denies_customers_before_model_or_writes(pilot_store, monkeypatch):
    app, _, users, actor, counts, _ = pilot_store
    model = AsyncMock(side_effect=AssertionError("客户不可调用人工推荐模型"))
    monkeypatch.setattr(workbench.client, "chat", model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for uid in ("u_a1", "u_aa", "u_b1", "u_ba"):
            actor["user"] = users[uid]
            before = counts()
            for path, body in (
                ("/api/workbench/conversations/c_u_a1/reply", {"content": "probe"}),
                ("/api/workbench/conversations/c_u_a1/takeover", {}),
                ("/api/workbench/tickets/t_u_a1", {"status": "closed"}),
            ):
                assert (await client.post(path, json=body)).status_code == 403
            assert (await client.get("/api/workbench/conversations/c_u_a1/suggest_reply")).status_code == 403
            assert (await client.get("/api/workbench/tickets/t_u_a1")).status_code == 403
            assert counts() == before
        model.assert_not_awaited()


async def test_sensitive_markers_do_not_enter_messages_tickets_traces_or_audit(pilot_store, monkeypatch):
    app, session, users, actor, _, session_factory = pilot_store
    secrets = ("sk-tiny", "fakeBearerToken", "pilot@example.test", "13912345678")
    text = "sk-tiny Bearer fakeBearerToken pilot@example.test 手机13912345678 request_id=req_20260615_8842"

    def clean(value):
        rendered = json.dumps(value, default=str, ensure_ascii=False)
        assert all(secret not in rendered for secret in secrets)

    run = AsyncMock(return_value={"answer": text, "intent": "chitchat", "card": {"conclusion": text},
                                  "citations": [{"doc_title": text}]})
    monkeypatch.setattr(chat.supervisor, "run", run)
    monkeypatch.setattr(tool_registry, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(trace_module, "AsyncSessionLocal", session_factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat", json={"conversation_id": "c_u_a1", "message": text})
        assert response.status_code == 200
        clean(response.text)
        clean(run.await_args)
        assert "req_20260615_8842" in str(run.await_args)
        conv = session.get(models.Conversation, "c_u_a1")
        conv.transferred_to_human = True
        session.commit()
        assert (await client.post("/api/chat", json={"conversation_id": conv.id, "message": text})).status_code == 200
        assert run.await_count == 1
        assert (await client.post(f"/api/conversations/{conv.id}/messages", json={"content": text})).status_code == 200
        assert (await client.post("/api/feedback", json={"conversation_id": conv.id, "type": "need_human"})).status_code == 200
        actor["user"] = users["support"]
        actor["user"].display_name = text
        assert (await client.post(f"/api/workbench/conversations/{conv.id}/reply", json={"content": text})).status_code == 200
        model = AsyncMock(return_value=ChatResult(content=text))
        monkeypatch.setattr(workbench.client, "chat", model)
        suggested = await client.get(f"/api/workbench/conversations/{conv.id}/suggest_reply")
        assert suggested.status_code == 200
        clean(suggested.json())
        clean(model.await_args)
        updated = await client.post("/api/workbench/tickets/t_u_a1", json={"assignee": "sk-tiny", "note": text})
        assert updated.status_code == 200
        clean(updated.json())
        clean((await client.get(f"/api/conversations/{conv.id}")).json())
    ctx = ToolContext("a", trace_id="trace_probe", user_id="u_a1", role="customer_dev")
    await tool_registry._log_call(ctx, "probe", {"note": text}, {"content": text}, "error", 1, text)
    trace = trace_module.TraceCollector("a", "c_u_a1")
    trace.step("probe", input_summary=text, output_summary=text, error=text, hit_docs=[text])
    await trace.persist()
    for model in (models.Message, models.Ticket, models.ToolCallLog, models.AgentTrace, models.AuditLog):
        rows = list(session.execute(select(model.__table__)).mappings())
        clean([dict(row) for row in rows])


async def test_input_sanitizer_failure_creates_no_conversation_or_message(pilot_store, monkeypatch):
    from app.guardrail import desensitize

    app, _, _, _, counts, _ = pilot_store

    def reject(_text):
        raise desensitize.SensitiveDataError()

    monkeypatch.setattr(desensitize, "desensitize_text", reject)
    run = AsyncMock(side_effect=AssertionError("不得外发"))
    monkeypatch.setattr(chat.supervisor, "run", run)
    before = counts()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(desensitize.SensitiveDataError):
            await client.post("/api/chat", json={"message": "probe"})
    assert counts() == before
    run.assert_not_awaited()
