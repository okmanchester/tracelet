"""统一建单，不自行提交；调用方将业务、接管和操作结果原子提交。"""

from hashlib import sha256

from sqlalchemy import select

from app import clock
from app.access import assert_conversation_access, require_identity
from app.guardrail import desensitize
from app.models import Ticket


def new_ticket_id(operation_key):
    return "tk_" + clock.now_utc().strftime("%Y%m%d") + "_" + operation_key[:20]


async def create_in_session(db, args, actor, operation_id, conv=None):
    require_identity(actor)
    if not operation_id:
        raise ValueError("缺少可信操作标识")
    if any(key in args and args[key] != value for key, value in (
        ("tenant_id", actor.tenant_id), ("user_id", actor.user_id),
    )):
        raise ValueError("工单身份与认证身份不一致")
    if conv is not None:
        assert_conversation_access(actor, conv, write=True)
    key = sha256((operation_id + ":ticket").encode()).hexdigest()
    existing = (await db.execute(select(Ticket).where(Ticket.operation_id == key))).scalar_one_or_none()
    if existing is not None:
        return existing
    clean = desensitize.desensitize_obj(args)
    ticket = Ticket(
        ticket_id=new_ticket_id(key),
        operation_id=key, tenant_id=actor.tenant_id, user_id=actor.user_id,
        conversation_id=conv.id if conv else None,
        category=clean.get("category", "其它"), priority=clean.get("priority", "P2"),
        status="new", title=clean.get("title", "技术支持工单")[:255],
        summary=clean.get("summary", ""), related_request_ids=clean.get("related_request_ids", []),
        related_endpoint=clean.get("related_endpoint"), error_code=clean.get("error_code"),
        evidence=clean.get("evidence", ""), ai_diagnosis=clean.get("ai_diagnosis", ""),
    )
    db.add(ticket)
    return ticket
