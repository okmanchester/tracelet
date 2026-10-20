"""所有线上消息入口共用 SQL 序号与版本；调用方持有会话行锁并负责提交。"""

import uuid

from sqlalchemy import select

from app.guardrail import desensitize
from app.models import Conversation, Message


async def locked_conversation(db, conversation_id: str) -> Conversation | None:
    return (await db.execute(select(Conversation).where(
        Conversation.id == conversation_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()


def append_message(db, conv: Conversation, role: str, content: str, meta=None) -> Message:
    conv.context_version = (conv.context_version or 0) + 1
    message = Message(id="msg_" + uuid.uuid4().hex, conversation_id=conv.id,
                      sequence=conv.context_version, role=role,
                      content=desensitize.desensitize_text(content),
                      meta=desensitize.desensitize_obj(meta or {}))
    db.add(message)
    return message


def revoke_ai(conv: Conversation) -> None:
    conv.transferred_to_human = True
    conv.control_version = (conv.control_version or 0) + 1
    conv.active_operation_id = None
    conv.lease_until = None
