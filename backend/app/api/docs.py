"""Authenticated browsing of immutable published knowledge snapshots."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import CurrentUser, get_current_user
from app.guardrail import desensitize
from app.knowledge import public_identifier, resolve_version
from app.models import KnowledgeChunk, KnowledgeSnapshotDocument

router = APIRouter(prefix="/api/docs", tags=["docs"])


def meta(document):
    return {"id": document.doc_id, "title": document.title, "category": document.category,
            "filename": document.filename, "knowledge_version": public_identifier(document.release_id)}


@router.get("")
async def list_docs(user: Annotated[CurrentUser, Depends(get_current_user)],
                    db: Annotated[AsyncSession, Depends(get_db)], knowledge_version: str | None = None):
    version = await resolve_version(db, knowledge_version)
    documents = list(await db.scalars(select(KnowledgeSnapshotDocument).where(
        KnowledgeSnapshotDocument.release_id == version).order_by(KnowledgeSnapshotDocument.doc_id)))
    return desensitize.desensitize_obj({"knowledge_version": public_identifier(version), "documents": [meta(d) for d in documents]})


@router.get("/{doc_id}")
async def get_doc(doc_id: str, user: Annotated[CurrentUser, Depends(get_current_user)],
                  db: Annotated[AsyncSession, Depends(get_db)], knowledge_version: str | None = None):
    version = await resolve_version(db, knowledge_version)
    document = await db.get(KnowledgeSnapshotDocument, (version, doc_id))
    if document is None:
        raise HTTPException(404, "文档不存在")
    chunks = list(await db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.release_id == version,
                  KnowledgeChunk.doc_id == doc_id).order_by(KnowledgeChunk.position)))
    return desensitize.desensitize_obj({**meta(document), "content": document.content,
        "chunks": [{"chunk_id": public_identifier(c.chunk_id, 64), "section": c.section, "content": c.content} for c in chunks]})
