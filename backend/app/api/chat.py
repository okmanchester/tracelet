"""聊天接收、执行和提交分离；SSE 只展示已提交结果。"""

import asyncio
import contextlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app import operations, turns
from app.agents import supervisor
from app.agents.ticket import build_args
from app.config import settings
from app.db import AsyncSessionLocal, get_db
from app.deps import CurrentUser, get_current_user
from app.guardrail import desensitize
from app.memory.context import load
from app.schemas.chat import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])


def response(result):
    if result["operation_status"] != "succeeded":
        return JSONResponse(status_code=202 if result["operation_status"] == "processing" else 503,
                            content=desensitize.desensitize_obj(result))
    async def stream():
        yield {"event": "meta", "data": json.dumps({
            key: result.get(key) for key in ("request_id", "conversation_id", "message_id", "intent", "trace_id")
        }, ensure_ascii=False)}
        answer = result.get("answer", "")
        for offset in range(0, len(answer), 18):
            yield {"event": "token", "data": answer[offset:offset + 18]}
        yield {"event": "done", "data": json.dumps(result, ensure_ascii=False)}
    return EventSourceResponse(stream())


async def known_or_unknown(actor, kind, request_id):
    try:
        async with AsyncSessionLocal() as fresh:
            result = await operations.query(fresh, actor, kind, request_id)
        return result
    except desensitize.SensitiveDataError:
        raise
    except Exception:
        return {"request_id": str(request_id), "operation_status": "unknown", "can_retry": False,
                "answer": "暂时无法确认处理结果，请保留原请求标识并稍后查询。"}


async def guarded_run(actor, handle, message, context):
    model = asyncio.create_task(supervisor.run(
        query=message, tenant_id=actor.tenant_id, user_id=actor.user_id,
        role=actor.role, is_internal=actor.is_internal, conversation_id=handle.conversation_id,
        operation_id=handle.operation_id, context=context, knowledge_version=handle.knowledge_version))
    lost = asyncio.Event()

    async def heartbeat():
        while True:
            await asyncio.sleep(settings.conversation_heartbeat_seconds)
            try:
                async with AsyncSessionLocal() as fresh:
                    valid = await operations.renew(fresh, handle)
            except Exception:
                valid = False
            if not valid:
                lost.set()
                model.cancel()
                return

    pulse = asyncio.create_task(heartbeat())
    try:
        return await asyncio.wait_for(model, timeout=settings.conversation_run_seconds)
    except asyncio.CancelledError:
        if lost.is_set():
            return None
        raise
    except TimeoutError:
        return {"answer": "技术服务处理超时。", "handoff_requested": True,
                "planned_ticket": build_args(query=message, intent="ticket", entities={},
                                            ai_diagnosis="", evidence={"failure": "timeout"})}
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    handle, draft = None, None
    try:
        message = desensitize.desensitize_text(body.message)
        handle, cached = await operations.receive(db, user, "chat", body.request_id,
            {"message": message, "conversation_id": body.conversation_id})
        if cached is not None:
            return response(cached)
        # 接收事务已提交；人工模式的请求直接交提交层确认，无模型调用。
        conv = await operations.locked_conversation(db, handle.conversation_id)
        human = conv.transferred_to_human
        await db.rollback()
        if human:
            draft = {}
        elif handle.draft:
            draft = handle.draft
        else:
            context = await load(handle.conversation_id, handle.user_message_id, db=db)
            await db.rollback()
            draft = await guarded_run(user, handle, message, context)
        result = await turns.finish(db, user, handle, draft)
        return response(result)
    except HTTPException:
        await db.rollback()
        raise
    except desensitize.SensitiveDataError:
        await db.rollback()
        raise
    except Exception:
        with contextlib.suppress(Exception):
            await db.rollback()
        if handle is not None:
            try:
                return response(await turns.recover_failure(user, handle, draft))
            except desensitize.SensitiveDataError:
                raise
            except Exception:
                pass
        return response(await known_or_unknown(user, "chat", body.request_id))


@router.get("/operations/{kind}/{request_id}")
async def operation_result(kind: Literal["chat", "feedback", "create_ticket"], request_id: UUID,
                           user: CurrentUser = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    return await operations.query(db, user, kind, request_id)
