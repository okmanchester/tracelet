# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""工单查询接口（客户侧：我的工单）+ 反馈接口。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import desc

from app.access import assert_conversation_access, ticket_read_filter
from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.guardrail import desensitize
from app.models import Conversation, Message, Ticket
from app.schemas.chat import FeedbackRequest

router = APIRouter(prefix="/api", tags=["tickets"])


@router.get("/tickets")
async def my_tickets(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    stmt = select(Ticket).order_by(Ticket.created_at.desc()).limit(50)
    stmt = stmt.where(ticket_read_filter(user))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "tickets": [
            {"ticket_id": t.ticket_id, "title": desensitize.desensitize_text(t.title), "category": t.category,
             "priority": t.priority, "status": t.status, "error_code": t.error_code,
             "created_at": t.created_at.isoformat()}
            for t in rows
        ]
    }


@router.get("/tickets/{ticket_id}")
async def ticket_detail(
    ticket_id: str, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    t = (await db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "工单不存在")
    allowed = (await db.execute(select(Ticket.ticket_id).where(
        Ticket.ticket_id == ticket_id, ticket_read_filter(user),
    ))).scalar_one_or_none()
    if allowed is None:
        raise HTTPException(403, "无权访问该工单")
    return {
        "ticket_id": t.ticket_id, "title": desensitize.desensitize_text(t.title), "category": t.category,
        "priority": t.priority, "status": t.status, "summary": desensitize.desensitize_text(t.summary),
        "related_request_ids": desensitize.desensitize_obj(t.related_request_ids),
        "related_endpoint": t.related_endpoint, "error_code": t.error_code,
        "ai_diagnosis": desensitize.desensitize_text(t.ai_diagnosis),
        "evidence": desensitize.desensitize_text(t.evidence), "assignee": t.assignee,
        "conversation_id": t.conversation_id, "created_at": t.created_at.isoformat(),
    }


@router.post("/feedback")
async def submit_feedback(body: FeedbackRequest, user: CurrentUser = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)) -> dict:
    from fastapi.responses import JSONResponse
    from app import operations, turns
    from app.api.chat import known_or_unknown

    handle, draft = None, None
    try:
        conv = (await db.execute(select(Conversation).where(
            Conversation.id == body.conversation_id))).scalar_one_or_none()
        if conv is None:
            raise HTTPException(404, "会话不存在")
        assert_conversation_access(user, conv, write=True)
        if body.message_id is not None:
            message = (await db.execute(select(Message.id).where(
                Message.id == body.message_id, Message.conversation_id == conv.id))).scalar_one_or_none()
            if message is None:
                raise HTTPException(422, "反馈消息不属于该会话")
        handle, cached = await operations.receive(db, user, "feedback", body.request_id,
            {"conversation_id": body.conversation_id, "message_id": body.message_id, "type": body.type})
        if cached is not None:
            result = cached
        else:
            draft = handle.draft or {"answer": "反馈已记录。"}
            if body.type == "need_human" and not handle.draft:
                last = (await db.execute(select(Message).where(
                    Message.conversation_id == body.conversation_id, Message.role == "user"
                ).order_by(Message.sequence.desc(), desc(Message.created_at)).limit(1))).scalars().first()
                title = desensitize.desensitize_text(last.content if last else "用户请求人工支持")[:60]
                draft = {"answer": "", "handoff_requested": True,
                         "planned_ticket": {"title": title, "summary": title,
                                            "category": "人工支持", "priority": "P2"}}
            await db.rollback()
            result = await turns.finish(db, user, handle, draft, feedback=body)
    except HTTPException:
        await db.rollback()
        raise
    except desensitize.SensitiveDataError:
        await db.rollback()
        raise
    except Exception:
        import contextlib
        with contextlib.suppress(Exception):
            await db.rollback()
        try:
            result = await turns.recover_failure(user, handle, draft) if handle else (
                await known_or_unknown(user, "feedback", body.request_id))
        except desensitize.SensitiveDataError:
            raise
        except Exception:
            result = await known_or_unknown(user, "feedback", body.request_id)
    if result["operation_status"] == "succeeded":
        return result
    return JSONResponse(status_code=202 if result["operation_status"] == "processing" else 503,
                        content=result)
