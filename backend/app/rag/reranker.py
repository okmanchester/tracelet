# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""重排序：对混合召回的候选用当前 rerank provider 精排，取 top_n。"""

from app.llm import client


async def rerank_candidates(
    query: str, candidates: list[dict], top_n: int = 5
) -> list[dict]:
    """对候选片段精排，返回带 rerank_score 的 top_n（降序）。"""
    if not candidates:
        return []
    docs = [c["content"] for c in candidates]
    results = await client.rerank(query, docs, top_n=top_n)
    reranked = []
    for r in results:
        item = dict(candidates[r["index"]])
        item["rerank_score"] = r["score"]
        reranked.append(item)
    return reranked
