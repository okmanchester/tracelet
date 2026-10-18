# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""OpenAI 兼容协议 provider：一套实现覆盖 DeepSeek / DashScope / 其他兼容厂商。

只依赖 openai SDK；厂商差异全部来自工厂注入的 api_key / base_url / model。
"""

import json
from collections.abc import AsyncGenerator

import httpx
import openai
from openai import AsyncOpenAI

from app.config import settings
from app.llm.base import (
    ChatResult,
    LLMConfigurationError,
    LLMError,
    LLMPermanentError,
    LLMTransientError,
)
from app.llm.registry import register_chat_provider, register_embedding_provider


def _map_openai_error(exc: Exception) -> LLMError:
    """把 OpenAI SDK 异常收敛为接入层统一异常，供门面决定是否重试。"""
    if isinstance(
        exc,
        (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return LLMTransientError(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return LLMTransientError(f"HTTP {exc.status_code}: {exc}")
        return LLMPermanentError(f"HTTP {exc.status_code}: {exc}")
    if isinstance(
        exc,
        (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.BadRequestError,
            openai.NotFoundError,
        ),
    ):
        return LLMPermanentError(f"{type(exc).__name__}: {exc}")
    return LLMPermanentError(f"{type(exc).__name__}: {exc}")


def _content_text(content: object) -> str:
    """兼容 content 为 str / None / 多模态列表三种形态。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    return str(content)


class OpenAICompatChatProvider:
    """OpenAI Chat Completions 兼容实现。"""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        model_small: str,
        model_large: str,
        timeout_seconds: float = 60.0,
        key_env: str = "",
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model_small = model_small
        self.model_large = model_large
        self.timeout_seconds = timeout_seconds
        self.key_env = key_env or f"{name.upper()}_API_KEY"
        self._client: AsyncOpenAI | None = None

    def model_for(self, tier: str) -> str:
        return self.model_small if tier == "small" else self.model_large

    def _ensure_client(self) -> AsyncOpenAI:
        if not self.api_key:
            raise LLMConfigurationError(f"{self.key_env} 未配置，无法调用 {self.name}")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(10.0, self.timeout_seconds)),
                max_retries=0,  # 重试统一交给门面的 tenacity，避免双重重试
            )
        return self._client

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ChatResult:
        model = model or self.model_large
        kwargs: dict = {"model": model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            resp = await self._ensure_client().chat.completions.create(**kwargs)
        except LLMError:
            raise
        except Exception as e:  # 统一映射后再抛
            raise _map_openai_error(e) from e

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                arguments = tc.function.arguments
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                tool_calls.append(
                    {"id": tc.id, "name": tc.function.name, "arguments": arguments}
                )
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        return ChatResult(
            content=_content_text(choice.message.content),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=getattr(resp, "model", None) or model,
        )

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]:
        model = model or self.model_large
        try:
            stream = await self._ensure_client().chat.completions.create(
                model=model, messages=messages, temperature=temperature, stream=True
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except LLMError:
            raise
        except Exception as e:
            raise _map_openai_error(e) from e


class OpenAICompatEmbeddingProvider:
    """OpenAI Embeddings 兼容实现；内部按厂商上限自动分批。"""

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int | None = None,
        max_batch_size: int = 16,
        timeout_seconds: float = 60.0,
        key_env: str = "",
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.dimensions = dimensions
        self.max_batch_size = max(1, max_batch_size)
        self.timeout_seconds = timeout_seconds
        self.key_env = key_env or f"{name.upper()}_API_KEY"
        self.fingerprint = f"{name}:{model}"
        self._client: AsyncOpenAI | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        if not self.api_key:
            raise LLMConfigurationError(f"{self.key_env} 未配置，无法调用 {self.name} embedding")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url or None,
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(10.0, self.timeout_seconds)),
                max_retries=0,
            )
        return self._client

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.max_batch_size):
            batch = texts[start : start + self.max_batch_size]
            kwargs: dict = {"model": self.model, "input": batch}
            if self.dimensions:
                kwargs["dimensions"] = self.dimensions
            try:
                resp = await self._ensure_client().embeddings.create(**kwargs)
            except LLMError:
                raise
            except Exception as e:
                raise _map_openai_error(e) from e
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend([list(map(float, d.embedding)) for d in ordered])
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text]))[0]


# ---------- 工厂与注册 ----------


def _build_deepseek() -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        name="deepseek",
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model_small=settings.deepseek_model_small,
        model_large=settings.deepseek_model_large,
        timeout_seconds=settings.llm_timeout_seconds,
        key_env="DEEPSEEK_API_KEY",
    )


def _build_dashscope_chat() -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        name="dashscope",
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model_small=settings.dashscope_model_small,
        model_large=settings.dashscope_model_large,
        timeout_seconds=settings.llm_timeout_seconds,
        key_env="DASHSCOPE_API_KEY",
    )


def _build_openai_compat_chat() -> OpenAICompatChatProvider:
    return OpenAICompatChatProvider(
        name="openai_compat",
        api_key=settings.openai_compat_api_key,
        base_url=settings.openai_compat_base_url,
        model_small=settings.openai_compat_model_small,
        model_large=settings.openai_compat_model_large,
        timeout_seconds=settings.llm_timeout_seconds,
        key_env="OPENAI_COMPAT_API_KEY",
    )


def _build_dashscope_embedding() -> OpenAICompatEmbeddingProvider:
    return OpenAICompatEmbeddingProvider(
        name="dashscope",
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.dashscope_embedding_model,
        dimensions=settings.embedding_dim,
        max_batch_size=10,  # DashScope 单次上限保守取 10
        timeout_seconds=settings.llm_timeout_seconds,
        key_env="DASHSCOPE_API_KEY",
    )


def _build_openai_compat_embedding() -> OpenAICompatEmbeddingProvider:
    return OpenAICompatEmbeddingProvider(
        name="openai_compat",
        api_key=settings.openai_compat_api_key,
        base_url=settings.openai_compat_base_url,
        model=settings.openai_compat_embedding_model,
        dimensions=None,  # 不强制传 dimensions，兼容不支持该参数的厂商
        max_batch_size=64,
        timeout_seconds=settings.llm_timeout_seconds,
        key_env="OPENAI_COMPAT_API_KEY",
    )


register_chat_provider("deepseek", _build_deepseek)
register_chat_provider("dashscope", _build_dashscope_chat)
register_chat_provider("openai_compat", _build_openai_compat_chat)
register_embedding_provider("dashscope", _build_dashscope_embedding)
register_embedding_provider("openai_compat", _build_openai_compat_embedding)
