# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""LLM 接入层协议：Chat / Embedding / Rerank 三类能力的统一边界。

厂商实现只需满足对应 Protocol，注册到 app.llm.registry 后即可通过 .env 切换。
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ChatResult:
    """一次对话调用的统一结果。"""

    content: str
    tool_calls: list[dict] = field(default_factory=list)  # OpenAI 风格 tool_calls
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


class LLMError(RuntimeError):
    """LLM 接入层统一异常基类。"""


class LLMConfigurationError(LLMError):
    """配置缺失、依赖未安装、模型维度不匹配等不可重试错误。"""


class LLMTransientError(LLMError):
    """超时/连接失败/限流/5xx 等可重试错误。"""


class LLMPermanentError(LLMError):
    """鉴权失败/参数错误/模型不存在等不可重试错误。"""


@runtime_checkable
class ChatProvider(Protocol):
    """对话能力。厂商只负责自己的模型 ID 与请求细节。"""

    name: str

    def model_for(self, tier: str) -> str:
        """按档位返回模型 ID：tier 为 "small" 或 "large"。"""
        ...

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> ChatResult: ...

    def chat_stream(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.3,
    ) -> AsyncGenerator[str, None]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """向量化能力。document 与 query 分开，便于适配需要查询指令前缀的模型。"""

    name: str
    fingerprint: str  # 向量空间标识，如 "local:bge-large-zh-v1.5"
    max_batch_size: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class RerankProvider(Protocol):
    """重排序能力。返回 [{index, score}]，按 score 降序。"""

    name: str

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict]: ...
