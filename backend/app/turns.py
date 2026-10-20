"""最终业务提交：在行锁和执行资格检查之后才创建工单或提交 AI 结果。"""

from sqlalchemy import select

from app import effects, ticket_service
from app.access import assert_conversation_access
from app.agents.security import has_action_language
from app.conversation_service import append_message, locked_conversation, revoke_ai
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import Feedback, Message, Ticket
from app.operations import db_now, locked_operation, owns, view

PUBLIC = {"answer", "intent", "confidence", "citations", "card", "trace_id", "total_tokens",
          "need_clarify", "from_cache"}
HUMAN_ACK = "会话已由人工技术支持处理，您的消息已保存，可在会话记录中查看进展。"


async def finish(db, actor, handle, draft, feedback=None):
    op = await locked_operation(db, handle.operation_id)
    conv = await locked_conversation(db, handle.conversation_id)
    assert_conversation_access(actor, conv, write=True)
    now = await db_now(db)
    if not owns(op, handle, now):
        result = view(op, can_retry=op.status == "failed" or (
            op.status == "processing" and (not op.lease_until or op.lease_until <= now)))
        await db.rollback()
        return result
    if feedback is None and (conv.transferred_to_human or (
        conv.control_version != handle.control_version or conv.active_operation_id != op.id
        or not conv.lease_until or conv.lease_until <= now
    )):
        result = {"answer": HUMAN_ACK if conv.transferred_to_human else "本轮执行已失效，请查询会话进展。",
                  "need_human": bool(conv.transferred_to_human), "human_mode": bool(conv.transferred_to_human),
                  "handoff_status": "completed" if conv.transferred_to_human else "superseded",
                  "ticket_id": conv.handoff_ticket_id,
                  "ticket_status": "created" if conv.handoff_ticket_id else "none",
                  "message_id": op.user_message_id, "discarded": True}
    else:
        if draft is None:
            result = view(op, can_retry=False)
            await db.rollback()
            return result
        draft = desensitize.desensitize_obj(draft)
        result = {key: value for key, value in draft.items() if key in PUBLIC}
        ticket_id = None
        needs_human = bool(draft.get("handoff_requested"))
        planned = draft.get("planned_ticket")
        # 模型不得为业务动作背书；正文、卡片和引用都在最终提交出口检查。
        action_language = has_action_language(result)
        if action_language:
            result.update(answer="", card=None, citations=[])
        if planned:
            reusable = None
            if needs_human and conv.transferred_to_human and conv.handoff_ticket_id:
                reusable = (await db.execute(select(Ticket).where(
                    Ticket.ticket_id == conv.handoff_ticket_id,
                    Ticket.conversation_id == conv.id, Ticket.tenant_id == conv.tenant_id,
                    Ticket.status.not_in(("closed", "resolved")),
                ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            if reusable is not None:
                ticket_id = reusable.ticket_id
            else:
                ticket = await ticket_service.create_in_session(db, planned, actor, op.id, conv)
                ticket_id = ticket.ticket_id
            if needs_human:
                revoke_ai(conv)
                conv.handoff_ticket_id = ticket_id
            confirmation = (f"已转人工，工单号 {ticket_id}。" if needs_human
                            else f"已创建跟进工单 {ticket_id}，您可以继续向 AI 提问。")
            result["answer"] = (result.get("answer", "") + "\n\n" + confirmation).strip()
            result["card"] = None
        elif action_language:
            result["answer"] = ("会话由人工技术支持处理。" if conv.transferred_to_human
                                else "本轮未创建工单或转人工，请继续提供问题详情。")
        result.update(ticket_id=ticket_id, need_human=bool(conv.transferred_to_human),
                      ticket_status="created" if ticket_id else "none",
                      handoff_status="completed" if needs_human and ticket_id else "not_requested")
        if feedback is not None:
            if feedback.message_id is not None:
                found = (await db.execute(select(Message.id).where(
                    Message.id == feedback.message_id, Message.conversation_id == conv.id))).scalar_one_or_none()
                if found is None:
                    from fastapi import HTTPException
                    raise HTTPException(422, "反馈消息不属于该会话")
            db.add(Feedback(conversation_id=conv.id, message_id=feedback.message_id,
                            tenant_id=conv.tenant_id, type=feedback.type))
            if feedback.type in {"resolved", "unresolved"}:
                conv.satisfaction = feedback.type
                conv.resolved_by_ai = feedback.type == "resolved"
            result["ok"] = True
        else:
            message = append_message(db, conv, "assistant", result.get("answer", ""), result)
            result["message_id"] = message.id
            conv.latest_intent = draft.get("intent")
            if "entities" in draft:
                conv.collected_entities = draft["entities"]
    effects.enqueue(db, op.id, conv.id, conv.tenant_id, (draft or {}).get("_effects"))
    op.result = desensitize.desensitize_obj(result)
    op.status, op.lease_until = "succeeded", None
    if conv.active_operation_id == op.id:
        conv.active_operation_id, conv.lease_until = None, None
    response = view(op)
    await db.commit()
    return response


async def recover_failure(actor, handle, draft):
    """新连接取得操作行锁：成功提交则复用；确认未提交才记录可重试失败。"""
    async with AsyncSessionLocal() as db:
        op = await locked_operation(db, handle.operation_id)
        if op.status == "succeeded" or op.owner_token != handle.owner_token or op.generation != handle.generation:
            return view(op)
        conv = await locked_conversation(db, handle.conversation_id) if handle.conversation_id else None
        if conv is not None:
            assert_conversation_access(actor, conv, write=True)
        op.status, op.lease_until = "failed", None
        op.draft = desensitize.desensitize_obj(draft or {})
        op.result = {"answer": "本次处理未完成，请使用原请求重试。", "can_retry": True,
                     "ticket_status": "failed", "handoff_status": "failed"}
        if conv is not None and conv.active_operation_id == op.id:
            conv.active_operation_id, conv.lease_until = None, None
        result = view(op, can_retry=True)
        await db.commit()
        return result
