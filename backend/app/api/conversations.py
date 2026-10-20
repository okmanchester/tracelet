# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""会话查询接口。"""

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_conversation_access, can_write_conversation, conversation_read_filter
from app import effects
from app.conversation_service import append_message, locked_conversation
from app.operations import db_now
from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.guardrail import desensitize
from app.models import Conversation, Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    stmt = select(Conversation).order_by(Conversation.updated_at.desc()).limit(50)
    stmt = stmt.where(conversation_read_filter(user))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "conversations": [
            {"id": c.id, "tenant_id": c.tenant_id, "status": c.status,
             "latest_intent": c.latest_intent, "transferred_to_human": c.transferred_to_human,
             "satisfaction": c.satisfaction, "updated_at": c.updated_at.isoformat()}
            for c in rows
        ]
    }


@router.get("/{conv_id}")
async def get_conversation(
    conv_id: str, user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    conv = (
        await db.execute(select(Conversation).where(Conversation.id == conv_id))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, "会话不存在")
    assert_conversation_access(user, conv)
    msgs = (
        await db.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(
                Message.sequence, Message.created_at, Message.id)
        )
    ).scalars().all()
    return {
        "conversation": {"id": conv.id, "tenant_id": conv.tenant_id, "status": conv.status,
                         "latest_intent": conv.latest_intent,
                         "transferred_to_human": conv.transferred_to_human,
                         "satisfaction": conv.satisfaction, "can_write": can_write_conversation(user, conv)},
        "messages": [
            {"id": m.id, "role": m.role, "content": desensitize.desensitize_text(m.content),
             "meta": desensitize.desensitize_obj(m.meta),
             "created_at": m.created_at.isoformat()}
            for m in msgs
        ],
    }


@router.post("/{conv_id}/messages")
async def add_customer_message(
    conv_id: str,
    content: str = Body(..., embed=True),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """客户在人工模式下补充消息（不走 AI），供「我的会话」回话使用。"""
    conv = await locked_conversation(db, conv_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    assert_conversation_access(user, conv, write=True)
    now = await db_now(db)
    if conv.active_operation_id and conv.lease_until and conv.lease_until > now:
        raise HTTPException(409, "会话正在处理，请稍后重试")
    msg = append_message(db, conv, "user", content)
    effects.enqueue(db, msg.id, conv.id, conv.tenant_id)
    await db.commit()
    return {"ok": True, "message_id": msg.id}
