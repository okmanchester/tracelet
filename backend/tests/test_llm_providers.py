# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""LLM 门面与 provider 注册表测试：不依赖真实厂商、不加载本地模型。"""

import pytest

from app.cache import semantic_cache
from app.config import settings
from app.llm import client, registry
from app.llm.base import ChatResult, LLMConfigurationError
from app.llm.router import model_for


class FakeChatProvider:
    name = "fake_chat"

    def model_for(self, tier: str) -> str:
        return "fake-small" if tier == "small" else "fake-large"

    async def chat(self, messages, *, model=None, tools=None, temperature=0.2):
        return ChatResult(content=f"echo:{model}", model=model or "fake-large")

    async def chat_stream(self, messages, *, model=None, temperature=0.3):
        yield "chunk"


class FakeEmbeddingProvider:
    name = "fake_embedding"
    fingerprint = "fake:model:v1"
    max_batch_size = 8

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    async def embed_documents(self, texts):
        return [[1.0] * self.dim for _ in texts]

    async def embed_query(self, text):
        return [2.0] * self.dim


class FakeRerankProvider:
    name = "fake_rerank"

    async def rerank(self, query, documents, top_n=None):
        return [{"index": 0, "score": 0.9}]


@pytest.fixture(autouse=True)
def _fake_providers():
    registry.register_chat_provider("fake_chat", FakeChatProvider)
    registry.register_embedding_provider("fake_embedding", FakeEmbeddingProvider)
    registry.register_embedding_provider("fake_bad", lambda: FakeEmbeddingProvider(dim=8))
    registry.register_rerank_provider("fake_rerank", FakeRerankProvider)
    registry.reset_providers()
    yield
    registry.reset_providers()


async def test_chat_delegates_to_selected_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_chat_provider", "fake_chat")
    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "echo:fake-large"


def test_model_for_uses_provider_model_mapping(monkeypatch):
    monkeypatch.setattr(settings, "llm_chat_provider", "fake_chat")
    assert model_for("intent") == "fake-small"
    assert model_for("diagnose") == "fake-large"


async def test_embed_documents_and_query_are_distinct(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "fake_embedding")
    docs = await client.embed_documents(["a", "b"])
    query = await client.embed_query("q")
    assert len(docs) == 2
    assert docs[0][0] == 1.0
    assert query[0] == 2.0
    assert len(query) == settings.embedding_dim


async def test_dimension_mismatch_raises_configuration_error(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "fake_bad")
    with pytest.raises(LLMConfigurationError):
        await client.embed_query("q")


async def test_rerank_delegates_to_provider(monkeypatch):
    monkeypatch.setattr(settings, "rerank_provider", "fake_rerank")
    result = await client.rerank("q", ["a"], top_n=1)
    assert result == [{"index": 0, "score": 0.9}]


def test_semantic_cache_key_contains_embedding_fingerprint(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "fake_embedding")
    assert semantic_cache.key_for("t1") == "semcache:fake:model:v1:t1"


async def test_missing_api_key_raises_configuration_error():
    from app.llm.providers.openai_compat import OpenAICompatChatProvider

    provider = OpenAICompatChatProvider(
        name="missing",
        api_key="",
        base_url="https://example.invalid",
        model_small="s",
        model_large="l",
    )
    with pytest.raises(LLMConfigurationError):
        await provider.chat([{"role": "user", "content": "hi"}])


def test_unknown_provider_reports_available_names(monkeypatch):
    monkeypatch.setattr(settings, "llm_chat_provider", "not_exists")
    with pytest.raises(LLMConfigurationError, match="deepseek"):
        registry.get_chat_provider()
