# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""Redis 客户端（异步），用于会话记忆、语义缓存、路由缓存。"""

from functools import lru_cache

import redis.asyncio as aioredis

from app.config import settings


@lru_cache
def get_redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)
