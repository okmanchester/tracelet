"""业务提交时登记附属任务；短事务领取，失败持久化，可由 CLI 重试。"""

import asyncio
import uuid
from datetime import timedelta
from hashlib import sha256

from sqlalchemy import or_, select

from app.config import settings
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import AgentTrace, EffectTask, TokenUsage
from app.operations import db_now


def enqueue(db, operation_id, conversation_id, tenant_id, effects=None):
    payloads = {**(effects or {}), "projection": {}}
    for kind, payload in payloads.items():
        db.add(EffectTask(id=sha256(f"{operation_id}:{kind}".encode()).hexdigest(),
            operation_id=operation_id, kind=kind,
            payload=desensitize.desensitize_obj({**payload, "tenant_id": tenant_id,
                                               "conversation_id": conversation_id})))


async def claim():
    async with AsyncSessionLocal() as db:
        now = await db_now(db)
        task = (await db.execute(select(EffectTask).where(
            EffectTask.available_at <= now,
            or_(EffectTask.status.in_(["pending", "retry"]),
                (EffectTask.status == "processing") & (EffectTask.lease_until <= now)),
        ).order_by(EffectTask.available_at).limit(1).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if task is None:
            return None
        if task.attempts >= 10:
            task.status, task.error, task.lease_until = "failed", "附属写入失败", None
            await db.commit()
            return None
        task.status = "processing"
        task.owner_token = uuid.uuid4().hex
        task.attempts += 1
        task.lease_until = now + timedelta(seconds=90)
        data = (task.id, task.owner_token, task.kind, dict(task.payload))
        await db.commit()
        return data


async def consume(claimed):
    task_id, owner, kind, payload = claimed
    try:
        if kind == "projection":
            from app.memory.context import rebuild
            await asyncio.wait_for(rebuild(payload["conversation_id"]), settings.cache_timeout_seconds)
        elif kind == "route_cache":
            from app.cache import route_cache
            await asyncio.wait_for(route_cache.put(payload["query"], payload["result"]), settings.cache_timeout_seconds)
        elif kind == "semantic_cache":
            from app.cache import semantic_cache
            if not payload.get("knowledge_version"):
                raise ValueError("历史答案缓存任务需要维护处理")
            await asyncio.wait_for(semantic_cache.put(payload["tenant_id"], payload["query"], payload["result"],
                                     payload["embedding"], operation_id=task_id,
                                     knowledge_version=payload["knowledge_version"]), settings.cache_timeout_seconds)
        async with AsyncSessionLocal() as db:
            task = (await db.execute(select(EffectTask).where(EffectTask.id == task_id)
                .with_for_update().execution_options(populate_existing=True))).scalar_one()
            now = await db_now(db)
            if task.owner_token != owner or task.status != "processing" or task.lease_until <= now:
                return
            if kind == "trace":
                for step in payload["steps"]:
                    db.add(AgentTrace(trace_id=payload["trace_id"], tenant_id=payload["tenant_id"],
                        conversation_id=payload["conversation_id"], **step))
            elif kind == "cost" and payload.get("total_tokens", 0) > 0:
                db.add(TokenUsage(tenant_id=payload["tenant_id"],
                    conversation_id=payload["conversation_id"], model="mixed",
                    total_tokens=payload["total_tokens"]))
            elif kind not in {"trace", "cost", "projection", "route_cache", "semantic_cache"}:
                raise ValueError("未知附属任务")
            task.status, task.error, task.lease_until = "done", None, None
            await db.commit()
    except Exception:  # noqa: BLE001 -- 附属故障必须落为可追踪、可重试任务。
        async with AsyncSessionLocal() as db:
            task = (await db.execute(select(EffectTask).where(EffectTask.id == task_id)
                .with_for_update().execution_options(populate_existing=True))).scalar_one()
            if task.owner_token == owner and task.status == "processing":
                now = await db_now(db)
                task.status = "failed" if task.attempts >= 10 else "retry"
                task.error = "附属写入失败"
                task.available_at = now + timedelta(seconds=min(60, 2 ** (task.attempts - 1)))
                task.lease_until = None
                await db.commit()


async def drain_once():
    task = await claim()
    if task is None:
        return False
    await consume(task)
    return True


async def worker():
    import logging
    while True:
        try:
            worked = await drain_once()
        except Exception:  # noqa: BLE001 -- worker 边界保留固定告警，不泄露原始载荷。
            logging.getLogger(__name__).warning("附属任务处理暂不可用")
            worked = False
        await asyncio.sleep(0 if worked else 2)
