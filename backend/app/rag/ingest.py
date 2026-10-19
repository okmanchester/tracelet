# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""知识库 ingest：读取 markdown → 切片 → 向量化 → 写入 Milvus，并登记文档元信息。"""

import re
from pathlib import Path

from app.db import SyncSessionLocal
from app.guardrail import desensitize
from app.llm import client
from app.llm.registry import get_embedding_provider
from app.models import KnowledgeDocument
from app.rag import store

# 知识库原始资料位于项目根目录 data/knowledge（与后端代码分离，便于运营维护与用户查看）
# ingest.py: parents[0]=rag, [1]=app, [2]=backend, [3]=项目根
KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "data" / "knowledge"

CATEGORY_MAP = {
    "01": "接入",
    "02": "鉴权",
    "03": "错误码",
    "04": "回调",
    "05": "限流",
    "06": "计费",
    "07": "数据质量",
    "08": "FAQ",
}

ERROR_CODE_RE = re.compile(r"^([A-Z][A-Z_]+)（")
MAX_CHARS = 600
OVERLAP = 80


def _category(filename: str) -> str:
    prefix = filename.split("-")[0]
    return CATEGORY_MAP.get(prefix, "其它")


def _split_long(text: str) -> list[str]:
    """长段落按字符窗口切分，带 overlap。"""
    if len(text) <= MAX_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - OVERLAP
    return chunks


def chunk_markdown(md: str, doc_title: str) -> list[dict]:
    """按 ## 章节切片，长章节再按窗口切分。"""
    lines = md.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title, buf = doc_title, []
    for line in lines:
        if line.startswith("## "):
            if buf:
                sections.append((current_title, buf))
            current_title, buf = line[3:].strip(), [line]
        elif line.startswith("# "):
            continue  # H1 作为文档标题，已单独处理
        else:
            buf.append(line)
    if buf:
        sections.append((current_title, buf))

    chunks = []
    for section_title, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        err_match = ERROR_CODE_RE.match(section_title)
        error_code = err_match.group(1) if err_match else ""
        for piece in _split_long(body):
            chunks.append(
                {"section": section_title, "content": piece.strip(), "error_code": error_code}
            )
    return chunks


async def ingest_all(recreate: bool = True) -> dict:
    """全量 ingest：重建 collection 并写入所有文档切片。返回统计。"""
    # 先用一次轻量调用预热 provider（本地模型加载可能失败），再动现有 collection
    print(f"[ingest] embedding provider: {get_embedding_provider().fingerprint}")
    await client.embed_documents(["warmup"])
    store.ensure_collection(recreate=recreate)
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    total_chunks = 0
    doc_records = []

    for f in files:
        md = desensitize.desensitize_text(f.read_text(encoding="utf-8"))
        first_line = md.splitlines()[0] if md else f.stem
        doc_title = first_line[2:].strip() if first_line.startswith("# ") else f.stem
        category = _category(f.name)
        chunks = chunk_markdown(md, doc_title)

        # 批量向量化（分批策略由当前 embedding provider 内部负责）
        contents = [c["content"] for c in chunks]
        embeddings = await client.embed_documents(contents)

        rows = []
        for c, emb in zip(chunks, embeddings):
            rows.append(
                {
                    "embedding": emb,
                    "content": c["content"],
                    "doc_title": doc_title,
                    "section": c["section"],
                    "category": category,
                    "error_code": c["error_code"],
                    "version": "v1",
                }
            )
        store.insert(rows)
        total_chunks += len(rows)
        doc_records.append(
            {
                "id": f"doc_{f.stem.split('-')[0]}",
                "title": doc_title,
                "category": category,
                "source_path": str(f.relative_to(KNOWLEDGE_DIR.parent.parent)),
                "chunk_count": len(rows),
            }
        )
        print(f"[ingest] {f.name} -> {len(rows)} chunks (category={category})")

    # 登记文档元信息到 MySQL
    with SyncSessionLocal() as s:
        for rec in doc_records:
            existing = s.get(KnowledgeDocument, rec["id"])
            if existing:
                existing.chunk_count = rec["chunk_count"]
                existing.title = rec["title"]
                existing.category = rec["category"]
                existing.source_path = rec["source_path"]
            else:
                s.add(KnowledgeDocument(status="published", version="v1", **rec))
        s.commit()

    return {"documents": len(files), "chunks": total_chunks}
