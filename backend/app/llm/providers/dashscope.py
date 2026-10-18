# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""DashScope 原生能力 provider：目前仅 Rerank（Chat/Embedding 走 OpenAI 兼容实现）。

使用 dashscope SDK 时显式传 api_key，避免旧实现写全局变量带来的多厂商冲突。
"""

import asyncio

from app.config import settings
from app.llm.base import (
    LLMConfigurationError,
    LLMPermanentError,
    LLMTransientError,
)
from app.llm.registry import register_rerank_provider


class DashScopeRerankProvider:
    """DashScope TextReRank 实现。"""

    def __init__(self, *, api_key: str, model: str) -> None:
        self.name = "dashscope"
        self.api_key = api_key
        self.model = model

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict]:
        if not documents:
            return []
        if not self.api_key:
            raise LLMConfigurationError("DASHSCOPE_API_KEY 未配置，无法调用 DashScope rerank")
        top_n = top_n if top_n and top_n > 0 else len(documents)

        def _call():
            import dashscope  # 延迟导入，保持 DashScope 为可选依赖

            return dashscope.TextReRank.call(
                model=self.model,
                query=query,
                documents=documents,
                top_n=top_n,
                return_documents=False,
                api_key=self.api_key,
            )

        try:
            resp = await asyncio.to_thread(_call)
        except Exception as e:
            raise LLMPermanentError(f"DashScope rerank 调用失败: {e}") from e

        status = getattr(resp, "status_code", 200)
        if status in (429, 500, 502, 503, 504):
            raise LLMTransientError(f"DashScope rerank HTTP {status}")
        if status != 200:
            message = getattr(resp, "message", "")
            raise LLMPermanentError(f"DashScope rerank HTTP {status}: {message}")

        results = []
        output = getattr(resp, "output", None)
        for r in getattr(output, "results", None) or []:
            results.append({"index": int(r.index), "score": float(r.relevance_score)})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results


def _build_dashscope_rerank() -> DashScopeRerankProvider:
    return DashScopeRerankProvider(
        api_key=settings.dashscope_api_key,
        model=settings.dashscope_rerank_model,
    )


register_rerank_provider("dashscope", _build_dashscope_rerank)
