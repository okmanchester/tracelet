"""Immutable knowledge artifacts and SQL-only publication transactions (ADR-0008)."""

import hashlib
import json
import math
import re
import struct
import threading
import time
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import SyncSessionLocal
from app.guardrail import desensitize
from app.models import (
    KnowledgeChunk,
    KnowledgeHead,
    KnowledgePublication,
    KnowledgeRelease,
    KnowledgeSnapshotDocument,
)
from app.rag.execution import RetrievalError


class KnowledgeError(RetrievalError):
    pass


class PublicationUnknown(KnowledgeError):
    pass


_artifact_signatures = {}
_artifact_lock = threading.Lock()


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def checked_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise KnowledgeError("knowledge_version_invalid")
    return value


def public_identifier(value, length=32):
    if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
        raise KnowledgeError("knowledge_identifier_invalid")
    return desensitize.PublicIdentifier(value)


def restore_result(result, release_id):
    """Rehydrate only trusted SQL operation/task or version-scoped cache metadata.

    JSON round trips erase the marker type. New model output must never call this
    helper; it receives markers only from the validated knowledge snapshot.
    """
    result = dict(result)
    if release_id is None or not re.fullmatch(r"[0-9a-f]{32}", str(release_id)):
        return result
    result["knowledge_version"] = public_identifier(release_id)
    citations = []
    for citation in result.get("citations", []):
        citation = dict(citation)
        if citation.get("version") == release_id and citation.get("doc_id") and re.fullmatch(r"[0-9a-f]{64}", str(citation.get("chunk_id", ""))):
            citation["version"] = public_identifier(release_id)
            citation["chunk_id"] = public_identifier(citation["chunk_id"], 64)
        citations.append(citation)
    if "citations" in result:
        result["citations"] = citations
    return result


def vector_digest(vector):
    if len(vector) != settings.embedding_dim or not all(math.isfinite(float(v)) for v in vector):
        raise KnowledgeError("knowledge_vector_invalid")
    return hashlib.sha256(struct.pack(f"<{len(vector)}f", *vector)).hexdigest()


@lru_cache(maxsize=4)
def _artifact_digest(root, signature):
    hasher = hashlib.sha256()
    for name, _, _ in signature:
        hasher.update(name.encode())
        with (Path(root) / name).open("rb") as source:
            while block := source.read(8 * 1024 * 1024):
                hasher.update(block)
    return hasher.hexdigest()


def embedding_config():
    from app.llm.registry import get_embedding_provider
    result = {"fingerprint": get_embedding_provider().fingerprint, "dim": settings.embedding_dim,
              "query_prefix": settings.local_embedding_query_prefix if settings.embedding_provider == "local" else "",
              "artifact": "external_revision_unverifiable"}
    if settings.embedding_provider == "local":
        root = Path(settings.local_embedding_model_path).resolve()
        if not root.is_dir():
            raise KnowledgeError("knowledge_model_unavailable")
        paths = sorted(p for p in root.rglob("*") if p.is_file() and ".cache" not in p.parts and ".git" not in p.parts)
        if not paths or any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in paths):
            raise KnowledgeError("knowledge_model_artifact_invalid")
        signature = tuple((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
        with _artifact_lock:
            previous = _artifact_signatures.setdefault(str(root), signature)
            if previous != signature:
                # A provider may already have old weights in memory. Rehashing the new
                # files alone would incorrectly certify that stale provider as compatible.
                raise KnowledgeError("knowledge_model_changed_restart_required")
        result["artifact"] = _artifact_digest(str(root), signature)
    return result


def _snapshot(db, release_id):
    documents = list(db.scalars(select(KnowledgeSnapshotDocument).where(
        KnowledgeSnapshotDocument.release_id == release_id).order_by(KnowledgeSnapshotDocument.doc_id)))
    chunks = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.release_id == release_id)
                             .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.position)))
    by_doc = {d.doc_id: d for d in documents}
    rows = []
    positions = {}
    for d in documents:
        if d.digest != digest(d.content):
            raise KnowledgeError("knowledge_document_digest_mismatch")
    for c in chunks:
        if c.doc_id not in by_doc or c.digest != digest(c.content):
            raise KnowledgeError("knowledge_chunk_digest_mismatch")
        position = positions.get(c.doc_id, 0)
        if c.position != position or c.chunk_id != digest([c.doc_id, position, c.digest]):
            raise KnowledgeError("knowledge_chunk_identity_mismatch")
        positions[c.doc_id] = position + 1
        d = by_doc[c.doc_id]
        rows.append({"doc_id": c.doc_id, "chunk_id": public_identifier(c.chunk_id, 64), "content": c.content,
                     "section": c.section, "error_code": c.error_code, "category": d.category,
                     "doc_title": d.title, "version": public_identifier(release_id), "vector_digest": c.vector_digest})
    if not documents or not rows or set(positions) != set(by_doc):
        raise KnowledgeError("knowledge_snapshot_empty")
    manifest = {"documents": [{"id": d.doc_id, "title": d.title, "category": d.category,
                               "filename": d.filename, "digest": d.digest} for d in documents],
                "chunks": [{"id": c.chunk_id, "doc_id": c.doc_id, "position": c.position,
                            "section": c.section, "error_code": c.error_code,
                            "digest": c.digest, "vector_digest": c.vector_digest} for c in chunks]}
    return rows, digest(manifest), len(documents)


def release_info(release_id, *, published=True, include_chunks=False):
    checked_version(release_id)
    config = embedding_config()
    with SyncSessionLocal() as db:
        release = db.get(KnowledgeRelease, release_id)
        if release is None or release.status != "ready":
            raise KnowledgeError("knowledge_version_unavailable")
        if published and db.scalar(select(KnowledgePublication.id).where(KnowledgePublication.release_id == release_id).limit(1)) is None:
            raise KnowledgeError("knowledge_version_unpublished")
        if release.manifest.get("embedding") != config:
            raise KnowledgeError("knowledge_embedding_incompatible")
        result = {"version": release_id, "collection": release.collection_name, "manifest": release.manifest}
        if include_chunks:
            rows, checksum, _ = _snapshot(db, release_id)
            if checksum != release.manifest.get("snapshot_digest"):
                raise KnowledgeError("knowledge_manifest_mismatch")
            result["chunks"] = [{k: v for k, v in row.items() if k != "vector_digest"} for row in rows]
        return result


async def build(source_dir=None):
    from app.llm import client
    from app.rag import store
    from app.rag.ingest import KNOWLEDGE_DIR, MAX_CHARS, OVERLAP, _category, chunk_markdown
    release_id = uuid4().hex
    collection = "knowledge_" + release_id
    config = embedding_config()
    documents = []
    ids = set()
    # Take the complete sanitized source snapshot before any embedding call.
    for path in sorted(Path(source_dir or KNOWLEDGE_DIR).glob("*.md")):
        doc_id = path.stem.split("-")[0]
        if (not re.fullmatch(r"[A-Za-z0-9_]{1,64}", doc_id) or doc_id in ids
                or desensitize.desensitize_text(doc_id) != doc_id):
            raise KnowledgeError("knowledge_document_identity_invalid")
        ids.add(doc_id)
        content = desensitize.desensitize_text(path.read_text(encoding="utf-8"))
        first = content.splitlines()[0] if content else ""
        title = first[2:].strip() if first.startswith("# ") else desensitize.desensitize_text(path.stem)
        filename = desensitize.desensitize_text(path.name)
        if not content.strip() or len(title.encode()) > 256 or len(filename) > 255:
            raise KnowledgeError("knowledge_document_invalid")
        documents.append(KnowledgeSnapshotDocument(release_id=release_id, doc_id=doc_id, title=title,
                         category=_category(path.name), filename=filename, content=content, digest=digest(content)))
    if not documents:
        raise KnowledgeError("knowledge_snapshot_empty")
    with SyncSessionLocal() as db:
        db.add(KnowledgeRelease(id=release_id, collection_name=collection, status="building", manifest={}, validation={}))
        db.flush()
        db.add_all(documents)
        db.commit()
    try:
        store.ensure_collection(collection_name=collection)
        for document in documents:
            pieces = chunk_markdown(document.content, document.title)
            if not pieces:
                raise KnowledgeError("knowledge_snapshot_empty")
            vectors = await client.embed_documents([c["content"] for c in pieces])
            if len(vectors) != len(pieces):
                raise KnowledgeError("knowledge_embedding_count_mismatch")
            rows, records = [], []
            for index, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
                checksum = digest(piece["content"])
                chunk_id = digest([document.doc_id, index, checksum])
                if len(piece["section"].encode()) > 256 or len(piece["content"].encode()) > 4000:
                    raise KnowledgeError("knowledge_chunk_too_large")
                records.append(KnowledgeChunk(release_id=release_id, chunk_id=chunk_id,
                    doc_id=document.doc_id, position=index, digest=checksum, vector_digest=vector_digest(vector), **piece))
                rows.append({**piece, "doc_id": document.doc_id, "chunk_id": chunk_id, "doc_title": document.title,
                             "category": document.category, "version": release_id, "embedding": vector})
            # Keep batches bounded for both SDK requests and failure recovery inspection.
            for offset in range(0, len(rows), 128):
                batch = rows[offset:offset+128]
                if store.insert(batch, collection_name=collection) != len(batch):
                    raise KnowledgeError("knowledge_insert_count_mismatch")
            with SyncSessionLocal() as db:
                db.add_all(records)
                db.commit()
        if embedding_config() != config:
            raise KnowledgeError("knowledge_embedding_changed_during_build")
        with SyncSessionLocal() as db:
            release = db.get(KnowledgeRelease, release_id)
            rows, checksum, count = _snapshot(db, release_id)
            release.manifest = {"snapshot_digest": checksum, "documents": count, "chunks": len(rows),
                                "embedding": config, "chunker": {"max_chars": MAX_CHARS, "overlap": OVERLAP},
                                "index": {"type": "HNSW", "metric": "COSINE", "M": 16, "efConstruction": 200}}
            release.status = "built"
            db.commit()
        return {"knowledge_version": release_id, "documents": count, "chunks": len(rows), "status": "built"}
    except BaseException:
        with SyncSessionLocal() as db:
            release = db.get(KnowledgeRelease, release_id)
            release.status, release.error = "failed", "knowledge_build_failed"
            db.commit()
        raise


def validate(release_id, timeout=60):
    from app.rag import store
    checked_version(release_id)
    config = embedding_config()
    with SyncSessionLocal() as db:
        release = db.get(KnowledgeRelease, release_id)
        if release is None or release.status not in {"built", "ready"}:
            raise KnowledgeError("knowledge_version_unavailable")
        if release.manifest.get("embedding") != config:
            raise KnowledgeError("knowledge_embedding_incompatible")
        expected, checksum, documents = _snapshot(db, release_id)
        manifest = dict(release.manifest)
        collection = release.collection_name
    if checksum != manifest.get("snapshot_digest") or len(expected) != manifest.get("chunks") or documents != manifest.get("documents"):
        raise KnowledgeError("knowledge_manifest_mismatch")
    deadline = time.monotonic() + timeout
    expected_by_id = {row["chunk_id"]: row for row in expected}
    while True:
        try:
            actual = store.snapshot(collection, deadline=deadline)
            if len(actual) != len(expected) or len({row["chunk_id"] for row in actual}) != len(expected):
                raise KnowledgeError("knowledge_vector_count_mismatch")
            for row in actual:
                vector = row.pop("embedding")
                checksum = vector_digest(vector)
                wanted = expected_by_id.get(row.get("chunk_id"))
                if wanted is None or {**row, "vector_digest": checksum} != wanted:
                    raise KnowledgeError("knowledge_vector_content_mismatch")
            break
        except desensitize.SensitiveDataError:
            raise
        except Exception as exc:  # noqa: BLE001 -- 索引可见性与加载故障在独立校验预算内重试。
            if time.monotonic() >= deadline:
                reason = str(exc) if isinstance(exc, KnowledgeError) else type(exc).__name__
                raise KnowledgeError("knowledge_validation_failed:" + reason) from None
            time.sleep(min(0.2, max(0, deadline-time.monotonic())))
    report = {"snapshot_digest": manifest["snapshot_digest"], "documents": documents,
              "chunks": len(expected), "validated_at": time.time()}
    with SyncSessionLocal() as db:
        release = db.get(KnowledgeRelease, release_id, with_for_update=True)
        if release.manifest != manifest or release.status not in {"built", "ready"}:
            raise KnowledgeError("knowledge_manifest_changed")
        release.status, release.validation, release.error = "ready", report, None
        db.commit()
    return {"knowledge_version": release_id, "status": "ready", **report}


def publication_view(row):
    return {"publication_id": row.id, "action": row.action, "knowledge_version": row.release_id,
            "previous_version": row.previous_release_id, "previous_generation": row.previous_generation,
            "generation": row.generation, "operator": row.operator}


def publication(publication_id):
    publication_id = str(UUID(str(publication_id)))
    with SyncSessionLocal() as db:
        row = db.get(KnowledgePublication, publication_id)
        return publication_view(row) if row else None


def publish(release_id, expected_generation, operator, publication_id, *, action="publish"):
    checked_version(release_id)
    publication_id = str(UUID(str(publication_id)))
    operator = desensitize.desensitize_text(operator).strip()
    if not operator or len(operator) > 128 or expected_generation < 0 or action not in {"publish", "rollback"}:
        raise KnowledgeError("knowledge_publication_invalid")
    fingerprint = digest([release_id, expected_generation, operator, action])
    with SyncSessionLocal() as db:
        old = db.get(KnowledgePublication, publication_id)
        if old is not None:
            if old.fingerprint != fingerprint:
                raise KnowledgeError("knowledge_publication_conflict")
            return publication_view(old)
    # All external work precedes the short publication transaction.
    validate(release_id)
    try:
        with SyncSessionLocal() as db:
            head = db.get(KnowledgeHead, 1, with_for_update=True)
            if head is None:
                raise KnowledgeError("knowledge_head_missing_run_migrations")
            old = db.scalar(select(KnowledgePublication).where(KnowledgePublication.id == publication_id).with_for_update())
            if old is not None:
                if old.fingerprint != fingerprint:
                    raise KnowledgeError("knowledge_publication_conflict")
                return publication_view(old)
            if head.generation != expected_generation:
                raise KnowledgeError("knowledge_generation_conflict")
            if action == "rollback" and db.scalar(select(KnowledgePublication.id).where(
                    KnowledgePublication.release_id == release_id).limit(1)) is None:
                raise KnowledgeError("knowledge_rollback_unpublished")
            release = db.get(KnowledgeRelease, release_id, with_for_update=True)
            if release.status != "ready":
                raise KnowledgeError("knowledge_version_unavailable")
            row = KnowledgePublication(id=publication_id, fingerprint=fingerprint, action=action,
                release_id=release_id, previous_release_id=head.release_id, previous_generation=head.generation,
                generation=head.generation+1, operator=operator)
            db.add(row)
            head.release_id, head.generation = release_id, head.generation+1
            result = publication_view(row)
            db.commit()
            return result
    except KnowledgeError:
        raise
    except Exception:  # noqa: BLE001 -- 提交异常必须从新连接确认原发布结果。
        try:
            result = publication(publication_id)
        except Exception:  # noqa: BLE001 -- 查询仍不可达时只能报告结果未知。
            raise PublicationUnknown("knowledge_publication_unknown:" + publication_id) from None
        if result is not None:
            return result
        raise KnowledgeError("knowledge_publication_not_committed:" + publication_id) from None


async def resolve_version(db, requested=None):
    if requested is None:
        head = await db.get(KnowledgeHead, 1)
        requested = head.release_id if head else None
        if requested is None:
            raise HTTPException(503, "知识服务尚未就绪")
    try:
        checked_version(requested)
    except KnowledgeError:
        raise HTTPException(422, "知识版本无效") from None
    release = await db.get(KnowledgeRelease, requested)
    known = await db.scalar(select(KnowledgePublication.id).where(KnowledgePublication.release_id == requested).limit(1))
    if release is None or release.status != "ready" or known is None:
        raise HTTPException(404, "知识版本不存在")
    return requested


async def current_version():
    """Explicit version selection for existing offline callers; not evaluation isolation."""
    from app.db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        return await resolve_version(db)
