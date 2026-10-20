"""仅供独立试用准入环境使用的合成数据和维护演练。"""

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta


def require_isolated():
    project = os.environ.get("TRACELET_PILOT_PROJECT", "")
    if (not project.startswith("tracelet-pilot-") or len(project) != 47
            or os.environ.get("MYSQL_DB") != project.replace("-", "_")
            or os.environ.get("MILVUS_COLLECTION") != "pilot_" + project[-32:]):
        raise RuntimeError("拒绝操作非独立准入环境")


def seed():
    require_isolated()
    from sqlalchemy import select

    from app.db import SyncSessionLocal
    from app.models import (
        ApiCallLog,
        ApiEndpoint,
        ApiKey,
        App,
        Conversation,
        ErrorCode,
        Plan,
        Tenant,
        User,
    )
    from app.security import hash_password
    from scripts.seed_data import ENDPOINTS, ERROR_CODES, PLANS

    password = os.environ["TRACELET_PILOT_LOGIN_PASSWORD"]
    now = datetime.now(UTC).replace(tzinfo=None)
    with SyncSessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            raise RuntimeError("合成数据装载只允许全新空库")
        db.add_all(Plan(**row) for row in PLANS)
        db.flush()
        db.add_all(Tenant(id=f"t_{suffix}", name=f"Pilot {suffix}", plan_id="plan_pro")
                   for suffix in ("a", "b", "platform"))
        db.flush()
        for tenant in ("a", "b"):
            for suffix, role in (("1", "customer_dev"), ("2", "customer_dev"), ("m", "customer_admin")):
                name = tenant + suffix
                db.add(User(id="u_" + name, tenant_id="t_" + tenant, username="pilot_" + name,
                            display_name="Pilot " + name, role=role, password_hash=hash_password(password)))
                db.add(Conversation(id="conv_" + name, tenant_id="t_" + tenant, user_id="u_" + name,
                                    transferred_to_human=True))
            db.add(App(id="app_" + tenant, tenant_id="t_" + tenant, name="Pilot App"))
        for role in ("support", "admin"):
            db.add(User(id="u_" + role, tenant_id="t_platform", username="pilot_" + role,
                        display_name="Pilot " + role, role=role, password_hash=hash_password(password)))
        db.flush()
        for tenant in ("a", "b"):
            db.add(ApiKey(id="key_" + tenant, app_id="app_" + tenant, tenant_id="t_" + tenant,
                          key_masked="ak_****pilot", status="EXPIRED", expire_at=now - timedelta(days=1)))
            db.add(ApiCallLog(request_id="req_pilot_" + tenant, tenant_id="t_" + tenant,
                              app_id="app_" + tenant, api_key_id="key_" + tenant,
                              endpoint=ENDPOINTS[0]["path"], http_status=401,
                              error_code="AUTH_KEY_EXPIRED", latency_ms=20, created_at=now))
        db.add_all(ApiEndpoint(**row) for row in ENDPOINTS)
        db.add_all(ErrorCode(**row) for row in ERROR_CODES)
        db.commit()


def knowledge_recovery():
    """Real failed build leaves active knowledge untouched; publish and rollback online."""
    require_isolated()
    from pathlib import Path
    from uuid import uuid4

    from app import knowledge
    from app.db import SyncSessionLocal
    from app.models import KnowledgeHead
    from app.rag import store
    with SyncSessionLocal() as db:
        head = db.get(KnowledgeHead, 1)
        original, generation = head.release_id, head.generation
    info = knowledge.release_info(original)
    # The preceding fault case restarts Milvus. Health/load returning is not
    # evidence that the active segments have become query-visible again.
    knowledge.validate(original, timeout=60)
    before = store.snapshot(info["collection"], deadline=time.monotonic()+60)
    if not before or len(before) != info["manifest"]["chunks"]:
        raise RuntimeError("生效知识基线未完整就绪，拒绝开始故障演练")
    report_path = Path(os.environ["TRACELET_PILOT_REPORT_DIR"])
    (report_path / "knowledge-backup.json").write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    real_insert = store.insert
    inserted = False
    def failing_insert(rows, *, collection_name):
        nonlocal inserted
        # A successful real insert followed by a real invalid-vector RPC in the candidate collection.
        result = real_insert(rows, collection_name=collection_name)
        inserted = True
        real_insert([{**rows[0], "embedding": [0.0]}], collection_name=collection_name)
        return result
    store.insert = failing_insert
    failed = False
    try:
        asyncio.run(knowledge.build())
    except Exception:
        failed = inserted
    finally:
        store.insert = real_insert
    after = store.snapshot(info["collection"], deadline=time.monotonic()+60)
    (report_path / "knowledge-after-failed-build.json").write_text(json.dumps(after, ensure_ascii=False), encoding="utf-8")
    def canonical(rows):
        return sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)
    with SyncSessionLocal() as db:
        assert db.get(KnowledgeHead, 1).release_id == original
    if not failed or canonical(before) != canonical(after):
        previous = {row["chunk_id"]: row for row in before}
        current = {row["chunk_id"]: row for row in after}
        changed = {key: sum(previous[chunk].get(key) != current[chunk].get(key) for chunk in previous.keys() & current.keys())
                   for key in set().union(*(row.keys() for row in before + after))}
        (report_path / "knowledge-failure-comparison.json").write_text(json.dumps({
            "injected_failure_observed": failed, "before_rows": len(before), "after_rows": len(after),
            "missing": len(previous.keys()-current.keys()), "extra": len(current.keys()-previous.keys()),
            "changed_fields": changed}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError("失败构建前后快照不一致，详见独立比较报告")
    from concurrent.futures import ThreadPoolExecutor

    import httpx
    from sqlalchemy import select

    from app.models import Operation
    urls = [os.environ["TRACELET_PILOT_API_URL"], os.environ["TRACELET_PILOT_SECOND_API_URL"]]
    with httpx.Client(trust_env=False, timeout=320) as http:
        token = http.post(urls[0]+"/api/auth/login", json={"username": "pilot_a1", "password": os.environ["TRACELET_PILOT_LOGIN_PASSWORD"]}).json()["access_token"]
        headers = {"Authorization": "Bearer "+token}
        def chat(url, request):
            response = http.post(url+"/api/chat", json=request, headers=headers)
            response.raise_for_status()
            lines = response.text.splitlines()
            for i, line in enumerate(lines):
                if line == "event: done":
                    return json.loads(lines[i+1][6:])
            raise RuntimeError("知识并发验收没有完成事件")
        def control(mode):
            response = http.post(os.environ["TRACELET_PILOT_CONTROL_URL"],
                headers={"X-Pilot-Control": os.environ["TRACELET_PILOT_CONTROL_SECRET"]},
                json={"action": "model", "mode": mode})
            response.raise_for_status()
        # Warm the second process on the old version before publishing.
        warm = chat(urls[1], {"request_id": str(uuid4()), "message": "Webhook 回调收不到怎么排查？请按文档说明。"})
        assert warm["knowledge_version"] == original and warm.get("citations")
        request_id = str(uuid4())
        # Existing history bypasses the first-turn answer cache, so the model hold
        # is a genuine in-flight barrier rather than an already completed replay.
        request = {"request_id": request_id, "conversation_id": warm["conversation_id"],
                   "message": "Webhook 回调收不到时如何排查？请按文档说明。"}
        control("hold")
        with ThreadPoolExecutor(1) as executor:
            running = executor.submit(chat, urls[0], request)
            try:
                deadline = time.monotonic()+30
                while True:
                    with SyncSessionLocal() as db:
                        op = db.scalar(select(Operation).where(Operation.request_id == request_id))
                        accepted = op is not None and op.knowledge_version == original
                    if accepted:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("旧版在途请求未被接受")
                    time.sleep(0.1)
                assert not running.done()
                candidate = asyncio.run(knowledge.build())["knowledge_version"]
                knowledge.validate(candidate)
                assert not running.done()
                knowledge.publish(candidate, generation, "pilot", uuid4())
                for url in urls:
                    listing = http.get(url+"/api/docs", headers=headers).json()
                    assert listing["knowledge_version"] == candidate
            finally:
                control("normal")
            old_result = running.result(timeout=320)
        assert old_result["knowledge_version"] == original
        assert old_result.get("citations") and all(c["version"] == original for c in old_result["citations"])
        assert chat(urls[1], request) == old_result
        for url in urls:
            result = chat(url, {"request_id": str(uuid4()), "message": "Webhook 回调收不到怎么排查？请按文档说明。"})
            assert result["knowledge_version"] == candidate and result.get("citations")
            assert all(c["version"] == candidate for c in result["citations"])
        rollback = knowledge.publish(original, generation+1, "pilot", uuid4(), action="rollback")
        for url in urls:
            assert http.get(url+"/api/docs", headers=headers).json()["knowledge_version"] == original
            assert http.get(url+"/api/docs", params={"knowledge_version": candidate}, headers=headers).json()["knowledge_version"] == candidate
            result = chat(url, {"request_id": str(uuid4()), "message": "Webhook 回调收不到怎么排查？请按文档说明。"})
            assert result["knowledge_version"] == original and result.get("citations")
            assert all(c["version"] == original for c in result["citations"])
    (report_path / "knowledge-recovery.json").write_text(json.dumps({
        "injected_failure": "invalid_vector_dimension_after_first_insert",
        "restored_chunks": len(after), "original_version": original, "published_version": candidate,
        "rollback": rollback, "active_artifact_unchanged": True,
        "application_processes": 2, "inflight_pinned": True, "cross_worker_uuid_replay": True,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    audit()


def snapshot_rows(client, collection, fields, expected, timeout=60):
    """历史固定小语料演练的就绪检查；当前版本校验使用 store.snapshot 完整迭代。"""
    if not 0 < expected <= 2000:
        raise RuntimeError("固定知识清单为空或超过当前完整检索边界，拒绝演练")
    deadline = time.monotonic() + timeout
    while True:
        rows = client.query(collection, filter="pk >= 0", output_fields=fields, limit=2000,
                            consistency_level="Strong", timeout=5)
        count = client.query(collection, filter="pk >= 0", output_fields=["count(*)"],
                             consistency_level="Strong", timeout=5)[0]["count(*)"]
        if count == expected == len(rows):
            return rows
        if time.monotonic() >= deadline:
            raise RuntimeError(f"固定知识快照不完整：expected={expected}, rows={len(rows)}, count={count}")
        time.sleep(0.5)


def knowledge_scale():
    """Complete real 2001-chunk artifacts and a labelled retrieval-only resource profile."""
    require_isolated()
    import platform
    import statistics
    from pathlib import Path
    from uuid import uuid4
    from app import knowledge
    from app.config import settings
    from app.db import SyncSessionLocal
    from app.models import KnowledgeHead
    from app.rag import execution, retriever
    report_dir = Path(os.environ["TRACELET_PILOT_REPORT_DIR"])
    source = report_dir / "scale-source"
    source.mkdir()
    text = "# 合成完整性语料\n" + "\n".join(
        f"## 合成章节 {i}\n检索完整性证据 probe_{i:04d}。" for i in range(2000))
    text += "\n## 尾部独有章节\n独有检索词 tailneedle2000 是第 2001 个切片的合成证据。"
    (source / "99-scale.md").write_text(text, encoding="utf-8")
    with SyncSessionLocal() as db:
        head = db.get(KnowledgeHead, 1)
        original, generation = head.release_id, head.generation
    release = asyncio.run(knowledge.build(source))["knowledge_version"]
    knowledge.validate(release)
    knowledge.publish(release, generation, "pilot-scale", uuid4())
    async def profile():
        results = []
        try:
            for workers in (1, 2, 4):
                settings.retrieval_workers, settings.retrieval_queue_capacity = workers, workers*2
                durations, lags, wall = [], [], []
                with execution.scope(release, deadline=time.monotonic()+300):
                    index = await retriever.get_index(release)
                    assert len(index[0]["chunks"]) == 2001
                    tail = next(c["chunk_id"] for c in index[0]["chunks"] if c["section"] == "尾部独有章节")
                    async def ticker():
                        while True:
                            start = time.monotonic()
                            await asyncio.sleep(0.01)
                            lags.append(max(0, time.monotonic()-start-0.01)*1000)
                    tick = asyncio.create_task(ticker())
                    sem = asyncio.Semaphore(workers)
                    async def one():
                        async with sem:
                            start = time.monotonic()
                            rows, _ = await retriever.hybrid_search("tailneedle2000", top_k_each=20)
                            assert tail in {c["chunk_id"] for c in rows}
                            durations.append(time.monotonic()-start)
                    try:
                        for _ in range(3):
                            start = time.monotonic()
                            await asyncio.gather(*(one() for _ in range(8)))
                            wall.append(time.monotonic()-start)
                    finally:
                        tick.cancel()
                        await asyncio.gather(tick, return_exceptions=True)
                    counts = dict(execution.pool().counts)
                await execution.close()
                ordered = sorted(durations)
                results.append({"workers": workers, "queue_capacity": workers*2, "requests": len(durations),
                    "median_round_seconds": statistics.median(wall), "query_p50_seconds": statistics.median(durations),
                    "query_p95_seconds": ordered[int((len(ordered)-1)*0.95)],
                    "max_sampled_loop_lag_ms": max(lags, default=0), "counts": counts})
            return results
        finally:
            await execution.close()
    try:
        profiles = asyncio.run(profile())
    finally:
        knowledge.publish(original, generation+1, "pilot-scale", uuid4(), action="rollback")
    (report_dir / "knowledge-scale.json").write_text(json.dumps({
        "knowledge_version": release, "chunks": 2001, "tail_keyword_recalled": True,
        "real_vector_full_validation": True, "profiles": profiles,
        "python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
        "scope": "retrieval-only: real local BGE/Milvus/BM25, fixed 2001 chunks, 8 jobs per round, 3 rounds, concurrency=W; not HTTP/LLM capacity",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def audit():
    """只输出合成标记命中计数，不输出任何存储载荷。"""
    require_isolated()
    from pathlib import Path

    import redis
    from sqlalchemy import String, cast, func, or_, select

    from app import models  # noqa: F401 -- 注册被检查的受管表。
    from app.config import settings
    from app.db import Base, SyncSessionLocal
    from app.rag import store

    marker = "sk-pilot-secret-1234567890abcdefghijk"
    sql_hits = {}
    with SyncSessionLocal() as db:
        for table in Base.metadata.sorted_tables:
            columns = [column for column in table.columns
                       if str(column.type).split("(")[0] in ("VARCHAR", "TEXT", "JSON")]
            if columns:
                count = db.scalar(select(func.count()).select_from(table).where(or_(
                    *(func.locate(marker, cast(column, String)) > 0 for column in columns))))
                sql_hits[table.name] = count
    cache = redis.Redis.from_url(settings.redis_url)
    redis_hits, redis_keys = 0, 0
    for key in cache.scan_iter():
        kind = cache.type(key)
        if kind == b"string":
            value = cache.get(key)
        elif kind == b"list":
            value = cache.lrange(key, 0, -1)
        elif kind == b"hash":
            value = cache.hgetall(key)
        elif kind == b"set":
            value = cache.smembers(key)
        elif kind == b"zset":
            value = cache.zrange(key, 0, -1)
        else:
            raise RuntimeError("未覆盖的 Redis 值类型，不能计为隐私验证通过")
        redis_hits += marker in repr(value)
        redis_keys += 1
    from app.models import KnowledgeRelease
    with SyncSessionLocal() as db:
        releases = list(db.scalars(select(KnowledgeRelease)))
    chunks = []
    collections_scanned = 0
    vector_client = store.get_client()
    for release in releases:
        if vector_client.has_collection(release.collection_name):
            # Failed builds can leave a partial artifact; privacy scans include it.
            chunks.extend(store.snapshot(release.collection_name, deadline=time.monotonic()+60))
            collections_scanned += 1
    if not chunks:
        raise RuntimeError("向量存储隐私检查范围不完整")
    result = {"sql_hits": sql_hits, "redis_keys_scanned": redis_keys, "redis_hits": redis_hits,
              "milvus_collections_scanned": collections_scanned,
              "milvus_chunks_scanned": len(chunks), "milvus_hits": sum(marker in str(row) for row in chunks)}
    (Path(os.environ["TRACELET_PILOT_REPORT_DIR"]) / "privacy.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    if any(sql_hits.values()) or redis_hits or result["milvus_hits"]:
        raise RuntimeError("合成敏感标记进入了持久化或缓存")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "knowledge-recovery", "knowledge-scale", "ingest", "audit"))
    args = parser.parse_args()
    require_isolated()
    if args.action == "seed":
        seed()
    elif args.action == "knowledge-recovery":
        knowledge_recovery()
    elif args.action == "knowledge-scale":
        knowledge_scale()
    elif args.action == "audit":
        audit()
    else:
        from scripts.setup_demo_knowledge import main as publish_demo
        publish_demo(["--yes"])


if __name__ == "__main__":
    main()
