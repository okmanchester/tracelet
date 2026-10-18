# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""LLM provider 注册表：按 .env 选择当前 Chat / Embedding / Rerank 实现。

用法：
- 内置 provider 在各模块末尾自注册；
- 新增厂商 = 实现类 + 一个工厂 + 一行 register_*，无需改门面；
- get_*_provider() 带 lru_cache，改 .env 后需重启进程。
"""

from collections.abc import Callable
from functools import lru_cache

from app.config import settings
from app.llm.base import (
    ChatProvider,
    EmbeddingProvider,
    LLMConfigurationError,
    RerankProvider,
)

ChatFactory = Callable[[], ChatProvider]
EmbeddingFactory = Callable[[], EmbeddingProvider]
RerankFactory = Callable[[], RerankProvider]

_CHAT_FACTORIES: dict[str, ChatFactory] = {}
_EMBEDDING_FACTORIES: dict[str, EmbeddingFactory] = {}
_RERANK_FACTORIES: dict[str, RerankFactory] = {}

_LOADED = False


def register_chat_provider(name: str, factory: ChatFactory) -> None:
    _CHAT_FACTORIES[name.strip().lower()] = factory


def register_embedding_provider(name: str, factory: EmbeddingFactory) -> None:
    _EMBEDDING_FACTORIES[name.strip().lower()] = factory


def register_rerank_provider(name: str, factory: RerankFactory) -> None:
    _RERANK_FACTORIES[name.strip().lower()] = factory


def load_providers() -> None:
    """延迟导入内置实现以触发注册，避免模块级循环依赖。"""
    global _LOADED
    if _LOADED:
        return
    from app.llm.providers import dashscope, local_bge, openai_compat  # noqa: F401

    _LOADED = True


def _resolve(factories: dict, selected: str, kind: str):
    load_providers()
    name = (selected or "").strip().lower()
    factory = factories.get(name)
    if factory is None:
        available = ", ".join(sorted(factories)) or "无"
        raise LLMConfigurationError(f"未知 {kind} provider: {selected!r}，可选: {available}")
    return factory()


@lru_cache
def get_chat_provider() -> ChatProvider:
    return _resolve(_CHAT_FACTORIES, settings.llm_chat_provider, "chat")


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    return _resolve(_EMBEDDING_FACTORIES, settings.embedding_provider, "embedding")


@lru_cache
def get_rerank_provider() -> RerankProvider:
    return _resolve(_RERANK_FACTORIES, settings.rerank_provider, "rerank")


def reset_providers() -> None:
    """测试用：清实例缓存（注册表本身保留）。"""
    get_chat_provider.cache_clear()
    get_embedding_provider.cache_clear()
    get_rerank_provider.cache_clear()
