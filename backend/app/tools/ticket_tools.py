# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""工单工具（真实写入 MySQL）+ 高风险操作占位（AI 不可直接执行）。"""

import uuid

from sqlalchemy import select

from app.access import (
    require_identity,
    ticket_read_filter,
    valid_identity,
)
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import Ticket
from app.tools.registry import ToolContext, ToolSpec, register


async def create_ticket(args: dict, ctx: ToolContext) -> dict:
    """独立写工具也必须有服务端操作标识，复用同一持久化建单服务。"""
    from app import operations, ticket_service, turns
    from app.conversation_service import locked_conversation

    require_identity(ctx)
    if any(key in args and args[key] != value for key, value in (
        ("tenant_id", ctx.tenant_id), ("user_id", ctx.user_id),
    )):
        raise ValueError("工单身份与认证身份不一致")
    if not ctx.operation_id:
        raise ValueError("缺少可信操作标识")
    request_id = uuid.uuid5(uuid.NAMESPACE_URL, ctx.operation_id + ":create_ticket")
    handle = None
    args = desensitize.desensitize_obj(args)
    try:
        async with AsyncSessionLocal() as db:
            handle, cached = await operations.receive(db, ctx, "create_ticket", request_id, args)
            if cached is not None:
                if cached["operation_status"] != "succeeded":
                    raise RuntimeError("工单操作仍在处理中")
                return {key: cached[key] for key in ("ticket_id", "status", "priority")}
            op = await operations.locked_operation(db, handle.operation_id)
            conv = await locked_conversation(db, handle.conversation_id) if handle.conversation_id else None
            now = await operations.db_now(db)
            if not operations.owns(op, handle, now):
                raise RuntimeError("工单操作已失效")
            if conv is not None and conv.active_operation_id and conv.active_operation_id != op.id:
                # 诊断中的建单只能由最终提交层完成，不能用独立工具绕过执行资格。
                raise RuntimeError("会话正在处理中")
            ticket = await ticket_service.create_in_session(db, args, ctx, op.id, conv)
            op.result = {"ticket_id": ticket.ticket_id, "status": ticket.status, "priority": ticket.priority}
            op.status, op.lease_until = "succeeded", None
            result = dict(op.result)
            await db.commit()
            return result
    except Exception:
        if handle is not None:
            recovered = await turns.recover_failure(ctx, handle, {"planned_ticket": args})
            if recovered["operation_status"] == "succeeded":
                return {key: recovered[key] for key in ("ticket_id", "status", "priority")}
        raise


async def query_ticket(args: dict, ctx: ToolContext) -> dict:
    """按 ticket_id 查询工单状态。"""
    if not valid_identity(ctx):
        return {"found": False}
    ticket_id = args.get("ticket_id")
    async with AsyncSessionLocal() as s:
        t = (
            await s.execute(select(Ticket).where(Ticket.ticket_id == ticket_id, ticket_read_filter(ctx)))
        ).scalar_one_or_none()
        if t is None:
            return {"found": False}
        return desensitize.desensitize_obj({"found": True, "ticket_id": t.ticket_id, "status": t.status,
                "priority": t.priority, "title": t.title})


async def _high_risk_blocked(args: dict, ctx: ToolContext) -> dict:
    return {"blocked": True, "reason": "高风险操作必须由人工或后台审批执行"}


register(ToolSpec(
    name="create_ticket",
    description="当问题无法自助解决、需人工介入或用户要求人工时，创建技术支持工单，附带 AI 诊断摘要与证据。",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "category": {"type": "string", "description": "如 API报错/套餐账单/数据质量/故障投诉"},
            "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
            "summary": {"type": "string"},
            "related_request_ids": {"type": "array", "items": {"type": "string"}},
            "related_endpoint": {"type": "string"},
            "error_code": {"type": "string"},
            "evidence": {"type": "string"},
            "ai_diagnosis": {"type": "string"},
        },
        "required": ["title", "category", "summary"],
    },
    func=create_ticket,
    category="ticket", mutating=True, idempotent=True,
))
register(ToolSpec(
    name="query_ticket",
    description="按 ticket_id 查询工单当前状态与优先级。",
    parameters={"type": "object", "properties": {"ticket_id": {"type": "string"}}, "required": ["ticket_id"]},
    func=query_ticket,
    category="ticket",
))

# ---- 高风险工具：注册但标记 high_risk，不暴露给 AI ----
for _name, _desc in [
    ("reset_api_key", "重置 API Key"),
    ("change_plan", "变更套餐"),
    ("refund", "退款/账单调整"),
]:
    register(ToolSpec(
        name=_name, description=_desc, parameters={"type": "object", "properties": {}},
        func=_high_risk_blocked, high_risk=True, category="high_risk",
    ))
