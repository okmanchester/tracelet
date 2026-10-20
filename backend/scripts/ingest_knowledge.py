# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""知识库 ingest 脚本：把 data/knowledge/*.md 切片向量化写入 Milvus。

用法（backend 目录下）：python -m scripts.ingest_knowledge
"""

import asyncio

from app.rag.ingest import ingest_all


def main() -> None:
    stats = asyncio.run(ingest_all())
    print(f"[ingest] 已构建 {stats['knowledge_version']}：文档 {stats['documents']} 篇，切片 {stats['chunks']} 个；尚未发布。")


if __name__ == "__main__":
    main()
