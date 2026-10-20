"""SQL 会话事实与带版本 Redis 投影；缓存失败不会改变上下文可用性。"""

import asyncio
import json
import logging

from sqlalchemy import or_, select

from app.cache.redis_client import get_redis
from app.config import settings
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import Conversation, Message

logger = logging.getLogger(__name__)
HISTORY_MAX = 20
TTL = 21600


async def _snapshot(db, conversation_id, before_message_id=None):
    conv = (await db.execute(select(Conversation).where(
        Conversation.id == conversation_id).execution_options(populate_existing=True))).scalar_one()
    boundary = None
    if before_message_id:
        boundary = (await db.execute(select(Message.sequence).where(
            Message.id == before_message_id, Message.conversation_id == conversation_id))).scalar_one()
    version = conv.context_version
    entities = desensitize.desensitize_obj(conv.collected_entities or {})
    try:
        raw = await asyncio.wait_for(get_redis().get(f"mem:v2:{conversation_id}"), settings.cache_timeout_seconds)
        cached = json.loads(raw) if raw else None
        if cached and cached["version"] == version:
            messages = cached["messages"]
            filtered = [m for m in messages if boundary is None or m["sequence"] is None
                        or m["sequence"] < boundary]
            if len(messages) < HISTORY_MAX + 1 or len(filtered) >= HISTORY_MAX:
                return {"version": version, "entities": entities,
                        "history": desensitize.desensitize_obj([
                            {"role": m["role"], "content": m["content"]}
                            for m in filtered[-HISTORY_MAX:]])}
    except desensitize.SensitiveDataError:
        raise
    except Exception:  # noqa: BLE001 -- 缓存解析与网络异常统一回退 SQL，安全异常已单独拒绝。
        logger.info("会话投影不可用，从数据库恢复")
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if boundary is not None:
        stmt = stmt.where(or_(Message.sequence < boundary, Message.sequence.is_(None)))
    rows = (await db.execute(stmt.order_by(Message.sequence.desc(), Message.created_at.desc(),
                                         Message.id.desc()).limit(HISTORY_MAX))).scalars().all()
    return {"version": version, "entities": entities,
            "history": desensitize.desensitize_obj([
                {"role": m.role, "content": m.content} for m in reversed(rows)])}


async def load(conversation_id, before_message_id=None, db=None):
    if db is not None:
        return await _snapshot(db, conversation_id, before_message_id)
    async with AsyncSessionLocal() as session:
        return await _snapshot(session, conversation_id, before_message_id)


async def rebuild(conversation_id):
    async with AsyncSessionLocal() as db:
        conv = (await db.execute(select(Conversation).where(
            Conversation.id == conversation_id))).scalar_one()
        rows = (await db.execute(select(Message).where(Message.conversation_id == conversation_id)
                                .order_by(Message.sequence.desc(), Message.created_at.desc(),
                                          Message.id.desc()).limit(HISTORY_MAX + 1))).scalars().all()
        payload = desensitize.desensitize_obj({"version": conv.context_version,
            "entities": conv.collected_entities or {}, "messages": [
                {"sequence": m.sequence, "role": m.role, "content": m.content}
                for m in reversed(rows)]})
    # 版本比较和覆盖原子执行，迟到的旧任务不能覆盖较新投影。
    return await get_redis().eval("""
        local raw = redis.call('GET', KEYS[1])
        if raw then
            local ok, old = pcall(cjson.decode, raw)
            if ok and tonumber(old.version) and tonumber(old.version) > tonumber(ARGV[1]) then
                return 0
            end
        end
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
    """, 1, f"mem:v2:{conversation_id}", payload["version"],
        json.dumps(payload, ensure_ascii=False), TTL)
