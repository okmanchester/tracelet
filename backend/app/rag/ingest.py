# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""知识库 ingest：读取 markdown → 切片 → 向量化 → 写入 Milvus，并登记文档元信息。"""

import re
from pathlib import Path

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


async def ingest_all(recreate: bool = False) -> dict:
    """Build a new immutable version; never recreate an active collection or publish."""
    from app.knowledge import build
    return await build()
