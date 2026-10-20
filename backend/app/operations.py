"""可信请求身份、短事务占用、租期与结果查询。模型运行不持有本模块的事务。"""

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert

from app import clock
from app.access import assert_conversation_access, require_identity
from app.config import settings
from app.conversation_service import append_message, locked_conversation
from app.guardrail import desensitize
from app.models import Conversation, EffectTask, Operation


def identity(actor, kind, request_id):
    request_id = str(uuid.UUID(str(request_id)))
    return sha256(json.dumps([actor.tenant_id, actor.user_id, kind, request_id]).encode()).hexdigest()


async def db_now(db):
    return clock.storage_utc((await db.execute(select(func.utc_timestamp(6)))).scalar_one())


async def locked_operation(db, operation_id):
    return (await db.execute(select(Operation).where(Operation.id == operation_id)
                            .with_for_update().execution_options(populate_existing=True))).scalar_one()


def view(op, *, can_retry=False):
    return desensitize.desensitize_obj({
        "request_id": op.request_id, "operation_status": op.status,
        "operation_id": "op_" + op.id,
        "conversation_id": op.conversation_id, "can_retry": can_retry,
        **(op.result or {}),
    })


@dataclass(frozen=True)
class Execution:
    operation_id: str
    owner_token: str
    generation: int
    conversation_id: str | None
    user_message_id: str | None
    control_version: int
    draft: dict


async def receive(db, actor, kind, request_id, payload):
    require_identity(actor)
    if settings.conversation_lease_seconds <= settings.conversation_heartbeat_seconds * 2:
        raise RuntimeError("会话租期必须大于两次续租间隔")
    payload = desensitize.desensitize_obj(payload)
    conv_id = payload.get("conversation_id")
    if conv_id is not None:
        conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one_or_none()
        if conv is None:
            raise HTTPException(404, "会话不存在")
        assert_conversation_access(actor, conv, write=True)
    fingerprint = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                   separators=(",", ":")).encode()).hexdigest()
    op_id = identity(actor, kind, request_id)
    now = await db_now(db)
    stmt = insert(Operation).values(
        id=op_id, tenant_id=actor.tenant_id, user_id=actor.user_id, kind=kind,
        request_id=str(request_id), fingerprint=fingerprint, status="processing",
        result={}, draft={}, request_payload=payload, generation=0, control_version=0, owner_token="",
        created_at=now, updated_at=now,
    )
    await db.execute(stmt.on_duplicate_key_update(id=stmt.inserted.id))
    op = await locked_operation(db, op_id)
    if op.fingerprint != fingerprint:
        await db.rollback()
        raise HTTPException(409, {"code": "request_conflict", "message": "请求标识已用于不同内容"})
    if op.conversation_id:
        conv_id = op.conversation_id
    conv = await locked_conversation(db, conv_id) if conv_id else None
    now = await db_now(db)
    if conv is not None:
        assert_conversation_access(actor, conv, write=True)
    if op.status == "succeeded":
        result = view(op)
        await db.rollback()
        return None, result
    if op.owner_token and op.status == "processing" and op.lease_until and op.lease_until > now:
        result = view(op)
        await db.rollback()
        return None, result
    if (kind == "chat" and conv is not None and conv.active_operation_id not in (None, op.id)
            and conv.lease_until and conv.lease_until > now):
        accepted = op.generation > 0
        await db.rollback()
        raise HTTPException(409, {"code": "conversation_busy", "message": "会话正在处理，请稍后重试",
                                  "request_accepted": accepted})
    if kind == "chat" and conv is None:
        conv = Conversation(id="conv_" + uuid.uuid4().hex, tenant_id=actor.tenant_id,
                            user_id=actor.user_id, context_version=0, control_version=0)
        db.add(conv)
        await db.flush()
    op.conversation_id = conv.id if conv else None
    if kind == "chat" and not op.user_message_id:
        op.user_message_id = append_message(db, conv, "user", payload["message"]).id
    op.status = "processing"
    op.result = {}
    op.owner_token = uuid.uuid4().hex
    op.generation += 1
    op.lease_until = now + timedelta(seconds=settings.conversation_lease_seconds)
    op.control_version = (conv.control_version or 0) if conv else 0
    if kind == "chat":
        conv.active_operation_id = op.id
        conv.lease_until = op.lease_until
    handle = Execution(op.id, op.owner_token, op.generation, op.conversation_id,
                       op.user_message_id, op.control_version, op.draft or {})
    await db.commit()
    return handle, None


def owns(op, handle, now):
    return (op.status == "processing" and op.owner_token == handle.owner_token
            and op.generation == handle.generation and op.lease_until is not None
            and op.lease_until > now)


async def renew(db, handle):
    op = await locked_operation(db, handle.operation_id)
    conv = await locked_conversation(db, handle.conversation_id) if handle.conversation_id else None
    now = await db_now(db)
    if not owns(op, handle, now) or (conv is not None and (
        conv.active_operation_id != op.id or conv.control_version != handle.control_version
        or conv.transferred_to_human
    )):
        await db.rollback()
        return False
    op.lease_until = now + timedelta(seconds=settings.conversation_lease_seconds)
    if conv is not None:
        conv.lease_until = op.lease_until
    await db.commit()
    return True


async def query(db, actor, kind, request_id):
    require_identity(actor)
    op_id = identity(actor, kind, request_id)
    op = (await db.execute(select(Operation).where(Operation.id == op_id)
                          .execution_options(populate_existing=True))).scalar_one_or_none()
    if op is None:
        raise HTTPException(404, "操作不存在")
    if op.conversation_id:
        conv = (await db.execute(select(Conversation).where(
            Conversation.id == op.conversation_id))).scalar_one_or_none()
        if conv is None:
            raise HTTPException(404, "操作不存在")
        assert_conversation_access(actor, conv, write=True)
    now = await db_now(db)
    result = view(op, can_retry=op.status == "failed" or (
        op.status == "processing" and (not op.lease_until or op.lease_until <= now)))
    result["retry_payload"] = desensitize.desensitize_obj(op.request_payload)
    result["effects"] = dict((await db.execute(select(EffectTask.status, func.count()).where(
        EffectTask.operation_id == op.id).group_by(EffectTask.status))).all())
    await db.rollback()
    return result
