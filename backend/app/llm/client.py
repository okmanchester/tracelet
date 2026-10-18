# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""LLM 门面：对业务层暴露稳定的 chat / embedding / rerank 接口。

具体厂商由 app.llm.registry 根据 .env 选择；本模块只负责：
- 统一重试策略（仅对 LLMTransientError，最多 3 次指数退避）；
- 统一向量维度校验；
- 保持历史函数签名，业务调用点无需感知 provider。
"""

from collections.abc import AsyncGenerator

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.llm import registry
from app.llm.base import ChatResult, LLMConfigurationError, LLMTransientError

__all__ = [
    "ChatResult",
    "chat",
    "chat_stream",
    "embed",
    "embed_documents",
    "embed_one",
    "embed_query",
    "rerank",
]

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type(LLMTransientError),
    reraise=True,
)


@_retry
async def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.2,
) -> ChatResult:
    """单轮/多轮对话。model 为空时使用当前 chat provider 的大模型。"""
    provider = registry.get_chat_provider()
    return await provider.chat(
        messages,
        model=model or provider.model_for("large"),
        tools=tools,
        temperature=temperature,
    )


async def chat_stream(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.3,
) -> AsyncGenerator[str, None]:
    """流式对话，逐段产出文本。"""
    provider = registry.get_chat_provider()
    async for chunk in provider.chat_stream(
        messages, model=model or provider.model_for("large"), temperature=temperature
    ):
        yield chunk


def _validate_dimension(vector: list[float]) -> None:
    """拦截 embedding 维度与 Milvus schema 不一致的情况，避免写坏索引。"""
    if len(vector) != settings.embedding_dim:
        raise LLMConfigurationError(
            f"embedding 维度 {len(vector)} 与 EMBEDDING_DIM={settings.embedding_dim} 不一致；"
            "请修改 .env 并重建 Milvus collection（python -m scripts.ingest_knowledge）"
        )


@_retry
async def embed(texts: list[str]) -> list[list[float]]:
    """批量文档向量化（ingest 用）。provider 内部按厂商上限自动分批。"""
    if not texts:
        return []
    vectors = await registry.get_embedding_provider().embed_documents(texts)
    for vector in vectors:
        _validate_dimension(vector)
    return vectors


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """语义更明确的别名：文档向量化。"""
    return await embed(texts)


@_retry
async def embed_query(text: str) -> list[float]:
    """查询向量化（检索 / 语义缓存用）。本地 BGE 会自动加 query 指令前缀。"""
    vector = await registry.get_embedding_provider().embed_query(text)
    _validate_dimension(vector)
    return vector


async def embed_one(text: str) -> list[float]:
    """兼容旧调用名，等价于 embed_query。"""
    return await embed_query(text)


@_retry
async def rerank(
    query: str, documents: list[str], top_n: int | None = None
) -> list[dict]:
    """文档重排序，返回 [{index, score}]，按相关性降序。"""
    if not documents:
        return []
    return await registry.get_rerank_provider().rerank(query, documents, top_n=top_n)
