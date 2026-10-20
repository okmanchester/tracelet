# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""Milvus 向量存储：knowledge_chunk collection 的管理与检索。

字段：
- pk           自增主键
- embedding    FLOAT_VECTOR(dim)，HNSW 索引，COSINE 度量
- content      原文片段
- doc_title    文档标题（引用 + 标量过滤）
- section      章节
- category     文档分类
- error_code   错误码（错误码类问题标量精确匹配）
- version      文档版本
"""

import threading

from pymilvus import DataType, MilvusClient

from app.config import settings
from app.rag.execution import remaining

VECTOR_DIM = settings.embedding_dim
COLLECTION = settings.milvus_collection


_local = threading.local()
_clients = []
_clients_lock = threading.Lock()


def get_client() -> MilvusClient:
    if not hasattr(_local, "client"):
        _local.client = MilvusClient(uri=settings.milvus_uri, timeout=remaining())
        with _clients_lock:
            _clients.append(_local.client)
    return _local.client


def close_clients():
    with _clients_lock:
        clients, _clients[:] = list(_clients), []
    for client in clients:
        client.close()
    if hasattr(_local, "client"):
        del _local.client


def ensure_collection(recreate: bool = False, *, collection_name=None) -> None:
    """创建 collection（含 HNSW 索引）。recreate=True 时先删除重建。"""
    collection = collection_name or COLLECTION
    client = get_client()
    if recreate and client.has_collection(collection):
        client.drop_collection(collection)
    if client.has_collection(collection):
        return

    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("pk", DataType.INT64, is_primary=True)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=VECTOR_DIM)
    schema.add_field("content", DataType.VARCHAR, max_length=4000)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=256)
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("category", DataType.VARCHAR, max_length=64)
    schema.add_field("error_code", DataType.VARCHAR, max_length=64)
    schema.add_field("version", DataType.VARCHAR, max_length=32)
    if collection_name is not None:
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=64)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    client.create_collection(
        collection_name=collection, schema=schema, index_params=index_params
    )


def drop_collection(*, collection_name=None) -> None:
    collection = collection_name or COLLECTION
    client = get_client()
    if client.has_collection(collection):
        client.drop_collection(collection)


def insert(rows: list[dict], *, collection_name=None) -> int:
    """批量插入切片。rows 每项含 embedding/content/doc_title/section/category/error_code/version。"""
    collection = collection_name or COLLECTION
    client = get_client()
    res = client.insert(collection_name=collection, data=rows)
    client.flush(collection)
    return res.get("insert_count", len(rows))


def count(*, collection_name=None) -> int:
    collection = collection_name or COLLECTION
    client = get_client()
    if not client.has_collection(collection):
        return 0
    client.load_collection(collection, timeout=remaining())
    res = client.query(collection, filter="pk >= 0", output_fields=["count(*)"])
    if res and "count(*)" in res[0]:
        return res[0]["count(*)"]
    return 0


def all_chunks(limit: int = 2000, *, collection_name=None) -> list[dict]:
    """旧集合的限量预览；版本校验使用 snapshot，BM25 读取完整 SQL 快照。"""
    collection = collection_name or COLLECTION
    client = get_client()
    if not client.has_collection(collection):
        return []
    client.load_collection(collection, timeout=remaining())
    return client.query(
        collection,
        filter="pk >= 0",
        output_fields=["content", "doc_title", "section", "category", "error_code", "version"] + (["doc_id", "chunk_id"] if collection_name else []),
        limit=limit,
    )


def search(
    query_vector: list[float], top_k: int = 20, expr: str | None = None, *, collection_name=None
) -> list[dict]:
    """向量检索，返回片段 + 相似度分数（COSINE，越大越相似）。"""
    collection = collection_name or COLLECTION
    client = get_client()
    client.load_collection(collection, timeout=remaining())
    results = client.search(
        collection_name=collection,
        data=[query_vector],
        limit=top_k,
        filter=expr or "",
        output_fields=["content", "doc_title", "section", "category", "error_code", "version"] + (["doc_id", "chunk_id"] if collection_name else []),
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        timeout=remaining(), consistency_level="Strong",
    )
    hits = []
    for hit in results[0]:
        entity = hit.get("entity", {})
        hits.append(
            {
                "score": float(hit.get("distance", 0.0)),
                "content": entity.get("content", ""),
                "doc_title": entity.get("doc_title", ""),
                "section": entity.get("section", ""),
                "category": entity.get("category", ""),
                "error_code": entity.get("error_code", ""),
                "version": entity.get("version", ""),
                "doc_id": entity.get("doc_id"), "chunk_id": entity.get("chunk_id"),
            }
        )
    return hits


def snapshot(collection, *, deadline):
    """Read and inspect a complete immutable artifact, never a limited preview."""
    client = get_client()
    schema = client.describe_collection(collection, timeout=remaining(deadline))
    fields = {field["name"]: field for field in schema["fields"]}
    required = {"pk", "embedding", "content", "doc_title", "section", "category", "error_code", "version", "doc_id", "chunk_id"}
    if set(fields) != required or int(fields["embedding"].get("params", {}).get("dim", 0)) != settings.embedding_dim:
        raise ValueError("knowledge_schema_mismatch")
    indexes = client.list_indexes(collection, timeout=remaining(deadline))
    descriptions = [client.describe_index(collection, name, timeout=remaining(deadline)) for name in indexes]
    if not any(d.get("index_type") == "HNSW" and d.get("metric_type") == "COSINE" for d in descriptions):
        raise ValueError("knowledge_index_mismatch")
    client.load_collection(collection, timeout=remaining(deadline))
    state = client.get_load_state(collection, timeout=remaining(deadline))["state"]
    if int(state) != 3:
        raise ValueError("knowledge_not_loaded")
    iterator = client.query_iterator(collection, batch_size=512, filter="pk >= 0",
        output_fields=sorted(required), consistency_level="Strong", timeout=remaining(deadline))
    rows = []
    try:
        while True:
            remaining(deadline)
            batch = iterator.next()
            if not batch:
                break
            # PyMilvus 2.4 QueryIterator uses the primary key as its cursor.
            # The physical auto-ID is not part of the immutable artifact identity.
            for row in batch:
                record = {key: value for key, value in row.items() if key != "pk"}
                # SDK iterator buffers may expose NumPy float32 scalars. Return
                # primitive values so backup/comparison has the same stable shape.
                record["embedding"] = [float(value) for value in record["embedding"]]
                rows.append(record)
    finally:
        iterator.close()
    count_result = client.query(collection, filter="pk >= 0", output_fields=["count(*)"],
                                consistency_level="Strong", timeout=remaining(deadline))
    if not count_result or count_result[0]["count(*)"] != len(rows):
        raise ValueError("knowledge_read_incomplete")
    return rows
