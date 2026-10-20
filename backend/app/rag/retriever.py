"""Version-pinned vector/BM25 retrieval with bounded synchronous work."""

import asyncio
import json
import time
from collections import OrderedDict

import jieba
from rank_bm25 import BM25Okapi

from app import knowledge
from app.config import settings
from app.llm import client
from app.rag import execution, store

_indexes = OrderedDict()


def _tokenize(text):
    return [t for t in jieba.lcut(text.lower()) if t.strip()]


def reset_bm25():
    _indexes.clear()


def _build_index(release_id):
    info = knowledge.release_info(release_id, include_chunks=True)
    return info, BM25Okapi([_tokenize(c["content"]) for c in info["chunks"]])


async def get_index(release_id):
    if release_id in _indexes:
        _indexes.move_to_end(release_id)
        return _indexes[release_id]
    loop = asyncio.get_running_loop()
    pending = getattr(loop, "_tracelet_bm25_pending", None)
    if pending is None:
        pending = loop._tracelet_bm25_pending = {}
    if release_id not in pending:
        async def build():
            try:
                result = await execution.run(lambda: _build_index(release_id), phase="bm25_build")
                _indexes[release_id] = result
                while len(_indexes) > settings.retrieval_bm25_cache_versions:
                    _indexes.popitem(last=False)
                return result
            finally:
                pending.pop(release_id, None)
        task = pending[release_id] = asyncio.create_task(build())
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    context = execution.current.get()
    timeout = max(0, context.deadline-time.monotonic()) if context else settings.conversation_run_seconds
    return await asyncio.wait_for(asyncio.shield(pending[release_id]), timeout)


def _rrf_fuse(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (k + rank)
    return scores


def _search(index, query, qvec, top_k_each, error_code):
    info, bm25 = index
    expr = "error_code == " + json.dumps(error_code, ensure_ascii=False) if error_code else None
    vec_hits = store.search(qvec, top_k=top_k_each, expr=expr, collection_name=info["collection"])
    if error_code and not vec_hits:
        vec_hits = store.search(qvec, top_k=top_k_each, collection_name=info["collection"])
    if not vec_hits:
        raise knowledge.KnowledgeError("knowledge_vector_unavailable")
    if any(h["version"] != info["version"] or not h.get("chunk_id") for h in vec_hits):
        raise knowledge.KnowledgeError("knowledge_vector_version_mismatch")
    snapshots = {c["chunk_id"]: c for c in info["chunks"]}
    for hit in vec_hits:
        original = snapshots.get(hit["chunk_id"])
        if original is None or any(hit.get(key) != original[key] for key in original):
            raise knowledge.KnowledgeError("knowledge_vector_content_mismatch")
        hit["version"], hit["chunk_id"] = original["version"], original["chunk_id"]
    scores = bm25.get_scores(_tokenize(query))
    positions = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k_each]
    bm25_hits = [info["chunks"][i] for i in positions if scores[i] > 0]
    by_id = {h["chunk_id"]: h for h in bm25_hits}
    by_id.update({h["chunk_id"]: h for h in vec_hits})
    fused = _rrf_fuse([[h["chunk_id"] for h in hits] for hits in (vec_hits, bm25_hits)])
    return ([{**by_id[key], "rrf_score": score} for key, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)],
            max(h["score"] for h in vec_hits))


async def hybrid_search(query, top_k_each=20, error_code=None):
    index = await get_index(execution.version())
    qvec = await client.embed_query(query)
    return await execution.run(lambda: _search(index, query, qvec, top_k_each, error_code), phase="hybrid_search")


async def error_document(error_code):
    from app.rag.compressor import build_context
    index = await get_index(execution.version())
    chunks = [c for c in index[0]["chunks"] if c["error_code"] == error_code]
    if not chunks:
        return None
    return build_context(chunks)
