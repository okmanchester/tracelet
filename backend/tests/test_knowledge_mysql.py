"""Publication and snapshot invariants on a fresh random MySQL database."""

import asyncio
import copy
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import knowledge, models
from app.rag import execution, retriever, store
from app.schema_migrations import migrate


@pytest.fixture
def artifacts(mysql_engine, monkeypatch, tmp_path):
    migrate(mysql_engine, "upgrade")
    factory = sessionmaker(mysql_engine, expire_on_commit=False)
    config = {"fingerprint": "synthetic", "dim": 2, "query_prefix": "", "artifact": "synthetic"}
    monkeypatch.setattr(knowledge, "SyncSessionLocal", factory)
    monkeypatch.setattr(knowledge, "embedding_config", lambda: config)
    monkeypatch.setattr(knowledge.settings, "embedding_dim", 2)
    from app.llm import client
    monkeypatch.setattr(client, "embed_documents", AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts]))
    vectors = {}
    monkeypatch.setattr(store, "ensure_collection", lambda **kwargs: vectors.setdefault(kwargs["collection_name"], []))
    def insert(rows, *, collection_name):
        vectors[collection_name].extend(copy.deepcopy(rows))
        return len(rows)
    monkeypatch.setattr(store, "insert", insert)
    monkeypatch.setattr(store, "snapshot", lambda collection, **kwargs: copy.deepcopy(vectors[collection]))
    source = tmp_path / "source"
    source.mkdir()
    (source / "01-auth.md").write_text("# 接入\n## AUTH_FAILED（鉴权错误）\n旧版本合成说明。", encoding="utf-8")
    return SimpleNamespace(factory=factory, source=source, vectors=vectors, config=config, client=client)


async def prepared(artifacts, *, publish=False, generation=0):
    result = await knowledge.build(artifacts.source)
    release_id = result["knowledge_version"]
    knowledge.validate(release_id)
    if publish:
        knowledge.publish(release_id, generation, "test", uuid4())
    return release_id


async def test_build_isolated_and_snapshots_do_not_follow_source(artifacts):
    first = await prepared(artifacts, publish=True)
    old = knowledge.release_info(first, include_chunks=True)
    (artifacts.source / "01-auth.md").write_text("# 接入\n## 新说明\n新版本。", encoding="utf-8")
    second = await prepared(artifacts)
    with artifacts.factory() as db:
        assert db.get(models.KnowledgeHead, 1).release_id == first
    assert knowledge.release_info(first, include_chunks=True) == old
    assert knowledge.release_info(second, published=False, include_chunks=True)["chunks"] != old["chunks"]
    assert len(artifacts.vectors) == 2


async def test_build_failure_keeps_current_and_retains_failed_artifact(artifacts, monkeypatch):
    first = await prepared(artifacts, publish=True)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(store, "insert", fail)
    with pytest.raises(RuntimeError):
        await knowledge.build(artifacts.source)
    with artifacts.factory() as db:
        assert db.get(models.KnowledgeHead, 1).release_id == first
        failed = db.scalar(select(models.KnowledgeRelease).where(models.KnowledgeRelease.status == "failed"))
        assert failed.error == "knowledge_build_failed" and failed.id != first


async def test_publication_replay_conflict_rollback_and_compare_swap(artifacts):
    first = await prepared(artifacts)
    request_id = uuid4()
    one = knowledge.publish(first, 0, "test", request_id)
    assert knowledge.publish(first, 0, "test", request_id) == one
    second = await prepared(artifacts)
    with pytest.raises(knowledge.KnowledgeError, match="publication_conflict"):
        knowledge.publish(second, 0, "test", request_id)
    with pytest.raises(knowledge.KnowledgeError, match="generation_conflict"):
        knowledge.publish(second, 0, "test", uuid4())
    with pytest.raises(knowledge.KnowledgeError, match="rollback_unpublished"):
        knowledge.publish(second, 1, "test", uuid4(), action="rollback")
    knowledge.publish(second, 1, "test", uuid4())
    assert knowledge.publish(first, 2, "test", uuid4(), action="rollback")["generation"] == 3
    with artifacts.factory() as db:
        assert db.scalar(select(func.count()).select_from(models.KnowledgePublication)) == 3


async def test_concurrent_publication_has_one_winner(artifacts):
    first, second = await prepared(artifacts), await prepared(artifacts)
    def attempt(release):
        try:
            return knowledge.publish(release, 0, "test", uuid4())
        except knowledge.KnowledgeError as exc:
            return str(exc)
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(attempt, (first, second)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "knowledge_generation_conflict" in results


async def test_commit_response_loss_recovers_same_publication(artifacts, monkeypatch):
    release = await prepared(artifacts)
    original = artifacts.factory
    class FailingCommit(original.class_):
        def commit(self):
            super().commit()
            if any(isinstance(value, models.KnowledgePublication) for value in self.identity_map.values()):
                raise OSError("commit response lost")
    # Only the publication transaction commits with a publication object attached.
    monkeypatch.setattr(knowledge, "SyncSessionLocal", sessionmaker(bind=original.kw["bind"], class_=FailingCommit, expire_on_commit=False))
    request_id = uuid4()
    result = knowledge.publish(release, 0, "test", request_id)
    assert result["generation"] == 1
    assert knowledge.publish(release, 0, "test", request_id) == result


@pytest.mark.parametrize("phase", ["build", "before_commit", "after_commit"])
async def test_process_exit_preserves_queryable_publication_boundary(artifacts, phase):
    first = await prepared(artifacts, publish=True)
    candidate = await prepared(artifacts)
    request_id = str(uuid4())
    # The child uses only this test's random SQL database. Vector validation is
    # separately covered; bypass it here to kill the actual process at SQL boundaries.
    code = '''
import asyncio, os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app import knowledge, models
from app.llm import client
from app.rag import store
engine = create_engine(os.environ["TRACELET_CRASH_TEST_URL"])
phase, candidate, request_id, source = sys.argv[1:]
class CrashSession(Session):
    def commit(self):
        publication = any(isinstance(row, models.KnowledgePublication) for row in self.new)
        if publication and phase == "before_commit":
            os._exit(73)
        super().commit()
        if publication and phase == "after_commit":
            os._exit(73)
knowledge.SyncSessionLocal = sessionmaker(engine, class_=CrashSession, expire_on_commit=False)
knowledge.embedding_config = lambda: {"fingerprint": "synthetic", "dim": 2, "query_prefix": "", "artifact": "synthetic"}
if phase == "build":
    store.ensure_collection = lambda **kwargs: None
    async def stopped_embedding(texts):
        os._exit(73)
    client.embed_documents = stopped_embedding
    asyncio.run(knowledge.build(source))
else:
    knowledge.validate = lambda release_id: None
    knowledge.publish(candidate, 1, "crash-test", request_id)
raise RuntimeError("crash boundary was not reached")
'''
    env = {**os.environ, "TRACELET_CRASH_TEST_URL": artifacts.factory.kw["bind"].url.render_as_string(hide_password=False)}
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-B", "-c", code, phase, candidate, request_id, str(artifacts.source)],
                            env=env, capture_output=True, timeout=30, check=False)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    with artifacts.factory() as db:
        head = db.get(models.KnowledgeHead, 1)
        assert head.release_id == (candidate if phase == "after_commit" else first)
        assert head.generation == (2 if phase == "after_commit" else 1)
        if phase == "build":
            interrupted = list(db.scalars(select(models.KnowledgeRelease).where(models.KnowledgeRelease.status == "building")))
            assert len(interrupted) == 1 and interrupted[0].id not in {first, candidate}
    committed = knowledge.publication(request_id)
    if phase == "after_commit":
        assert committed["knowledge_version"] == candidate
        assert knowledge.publish(candidate, 1, "crash-test", request_id) == committed
    else:
        assert committed is None
    assert knowledge.release_info(first, include_chunks=True)["chunks"]


@pytest.mark.parametrize("phase", ["embedding", "sql_commit"])
async def test_explicit_build_or_sql_failure_keeps_published_head(artifacts, monkeypatch, phase):
    first = await prepared(artifacts, publish=True)
    candidate = await prepared(artifacts)
    request_id = str(uuid4())
    if phase == "embedding":
        monkeypatch.setattr(artifacts.client, "embed_documents", AsyncMock(side_effect=OSError("synthetic embedding failure")))
        with pytest.raises(OSError):
            await knowledge.build(artifacts.source)
    else:
        class FailedCommit(artifacts.factory.class_):
            def commit(self):
                if any(isinstance(row, models.KnowledgePublication) for row in self.new):
                    raise OSError("synthetic SQL failure")
                super().commit()
        monkeypatch.setattr(knowledge, "SyncSessionLocal", sessionmaker(
            artifacts.factory.kw["bind"], class_=FailedCommit, expire_on_commit=False))
        with pytest.raises(knowledge.KnowledgeError, match="publication_not_committed"):
            knowledge.publish(candidate, 1, "test", request_id)
        assert knowledge.publication(request_id) is None
    with artifacts.factory() as db:
        head = db.get(models.KnowledgeHead, 1)
        assert (head.release_id, head.generation) == (first, 1)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "body", "vector"])
async def test_validation_rejects_corrupt_vectors_without_publishing(artifacts, corruption):
    result = await knowledge.build(artifacts.source)
    release_id = result["knowledge_version"]
    rows = artifacts.vectors["knowledge_"+release_id]
    if corruption == "missing":
        rows.clear()
    elif corruption == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif corruption == "body":
        rows[0]["content"] = "wrong snapshot"
    else:
        rows[0]["embedding"] = [float("nan"), 0.0]
    with pytest.raises(knowledge.KnowledgeError, match="validation_failed"):
        knowledge.validate(release_id, timeout=0.01)
    with artifacts.factory() as db:
        assert db.get(models.KnowledgeRelease, release_id).status == "built"
        assert db.get(models.KnowledgeHead, 1).release_id is None


async def test_2001_chunks_and_duplicate_content_have_stable_distinct_sources(artifacts, monkeypatch):
    text = "# 文档\n" + "\n".join(f"## section_{i}\n合成完整性中文说明 unique_{i}" for i in range(2001))
    assert len(text.encode()) > 65535  # A complete source snapshot must exceed MySQL TEXT's byte limit safely.
    (artifacts.source / "01-auth.md").write_text(text, encoding="utf-8")
    (artifacts.source / "02-copy.md").write_text("# 另一来源\n## section_0\n合成完整性中文说明 unique_0", encoding="utf-8")
    release = await prepared(artifacts, publish=True)
    info = knowledge.release_info(release, include_chunks=True)
    assert len(info["chunks"]) == 2002
    target = next(c for c in info["chunks"] if c["section"] == "section_2000")
    def vector_search(*args, **kwargs):
        return [{**info["chunks"][0], "score": 0.1}]
    monkeypatch.setattr(store, "search", vector_search)
    monkeypatch.setattr(artifacts.client, "embed_query", AsyncMock(return_value=[1.0, 0.0]))
    with execution.scope(release):
        results, _ = await retriever.hybrid_search("unique_2000", top_k_each=20)
    assert target["chunk_id"] in {c["chunk_id"] for c in results}
    copies = [c for c in info["chunks"] if c["section"] == "section_0"]
    assert len({c["chunk_id"] for c in copies}) == 2


async def test_configuration_mismatch_and_unpublished_version_are_rejected(artifacts, monkeypatch):
    release = await prepared(artifacts)
    with pytest.raises(knowledge.KnowledgeError, match="unpublished"):
        knowledge.release_info(release)
    knowledge.publish(release, 0, "test", uuid4())
    monkeypatch.setattr(knowledge, "embedding_config", lambda: {"dim": 123})
    with pytest.raises(knowledge.KnowledgeError, match="incompatible"):
        knowledge.release_info(release)


async def test_input_sanitized_before_snapshot_and_empty_rejected(artifacts):
    marker = "sk-pilot-secret-1234567890abcdefghijk"
    (artifacts.source / "01-auth.md").write_text("# 合成\n## 安全\napi_key=" + marker, encoding="utf-8")
    release = await prepared(artifacts)
    info = knowledge.release_info(release, published=False, include_chunks=True)
    assert marker not in str(info)
    assert marker not in str(artifacts.vectors)
    (artifacts.source / "01-auth.md").unlink()
    with pytest.raises(knowledge.KnowledgeError, match="empty"):
        await knowledge.build(artifacts.source)


async def test_document_api_reads_exact_published_snapshot(artifacts):
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api import docs
    from app.db import get_db
    from app.deps import CurrentUser, get_current_user
    first = await prepared(artifacts, publish=True)
    (artifacts.source / "01-auth.md").write_text("# 新文档\n## 新章节\n新版本内容", encoding="utf-8")
    second = await prepared(artifacts, publish=True, generation=1)
    engine = create_async_engine(artifacts.factory.kw["bind"].url.set(drivername="mysql+aiomysql"), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(docs.router)
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser("u", "u", "u", "customer_dev", "a")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            listing = (await client.get("/api/docs")).json()
            assert listing["knowledge_version"] == second
            old = (await client.get("/api/docs/01", params={"knowledge_version": first})).json()
            assert old["knowledge_version"] == first and "旧版本" in old["content"]
            assert old["chunks"][0]["chunk_id"]
            current = (await client.get("/api/docs/01")).json()
            assert "新版本" in current["content"]
            assert (await client.get("/api/docs", params={"knowledge_version": "v1"})).status_code == 422
            assert (await client.get("/api/docs", params={"knowledge_version": "f"*32})).status_code == 404
    finally:
        await engine.dispose()


async def test_source_filename_and_fallback_title_are_sanitized(artifacts):
    marker = "sk-pilot-secret-1234567890abcdefghijk"
    (artifacts.source / "01-auth.md").unlink()
    (artifacts.source / f"01-{marker}.md").write_text("普通合成说明。", encoding="utf-8")
    release = await prepared(artifacts)
    with artifacts.factory() as db:
        document = db.get(models.KnowledgeSnapshotDocument, (release, "01"))
        assert marker not in document.filename and marker not in document.title
