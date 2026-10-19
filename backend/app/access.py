"""会话与工单的统一访问策略；身份只能由服务端构造。"""

from typing import Protocol

from fastapi import HTTPException
from sqlalchemy import and_, exists, false, or_, select, true

from app.models import Conversation, Ticket

CUSTOMER_ROLES = {"customer_dev", "customer_admin"}
INTERNAL_ROLES = {"support", "admin"}


class Identity(Protocol):
    tenant_id: str
    user_id: str
    role: str


def valid_identity(user: Identity) -> bool:
    return bool(user.tenant_id and user.user_id and user.role in CUSTOMER_ROLES | INTERNAL_ROLES)


def is_internal(user: Identity) -> bool:
    return valid_identity(user) and user.role in INTERNAL_ROLES


def require_identity(user: Identity) -> None:
    if not valid_identity(user):
        raise HTTPException(403, "身份或角色无效")


def can_read_conversation(user: Identity, conv: Conversation) -> bool:
    return valid_identity(user) and (
        is_internal(user) or (
            conv.tenant_id == user.tenant_id
            and (user.role == "customer_admin" or conv.user_id == user.user_id)
        )
    )


def can_write_conversation(user: Identity, conv: Conversation) -> bool:
    return valid_identity(user) and conv.tenant_id == user.tenant_id and conv.user_id == user.user_id


def assert_conversation_access(user: Identity, conv: Conversation, *, write: bool = False) -> None:
    allowed = can_write_conversation(user, conv) if write else can_read_conversation(user, conv)
    if not allowed:
        raise HTTPException(403, "无权操作该会话" if write else "无权访问该会话")


def conversation_read_filter(user: Identity):
    if not valid_identity(user):
        return false()
    if is_internal(user):
        return true()
    tenant = Conversation.tenant_id == user.tenant_id
    return tenant if user.role == "customer_admin" else and_(tenant, Conversation.user_id == user.user_id)


def ticket_read_filter(user: Identity):
    if not valid_identity(user):
        return false()
    if is_internal(user):
        return true()
    tenant = Ticket.tenant_id == user.tenant_id
    if user.role == "customer_admin":
        return tenant
    matching_conversation = exists(select(1).where(
        Conversation.id == Ticket.conversation_id,
        Conversation.tenant_id == Ticket.tenant_id,
        Conversation.user_id == Ticket.user_id,
    ).correlate(Ticket))
    return and_(tenant, Ticket.user_id == user.user_id, or_(
        Ticket.conversation_id.is_(None), matching_conversation,
    ))
