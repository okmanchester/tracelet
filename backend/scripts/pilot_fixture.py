"""仅供独立试用准入环境使用的合成数据和维护演练。"""

import argparse
import asyncio
import hashlib
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
    """停服期间保存可用快照，制造真实插入失败，再从快照恢复。"""
    require_isolated()
    import redis
    from sqlalchemy import func, select

    from app.config import settings
    from app.db import SyncSessionLocal
    from app.models import KnowledgeDocument
    from app.rag import store
    from app.rag.ingest import KNOWLEDGE_DIR

    client = store.get_client()
    fields = ["embedding", "content", "doc_title", "section", "category", "error_code", "version"]
    client.load_collection(store.COLLECTION)
    with SyncSessionLocal() as db:
        original = int(db.scalar(select(func.sum(KnowledgeDocument.chunk_count))) or 0)
    rows = snapshot_rows(client, store.COLLECTION, fields, original)
    rows = [{key: row[key] for key in fields} for row in rows]
    snapshot = [{**row, "embedding": [float(value) for value in row["embedding"]]} for row in rows]
    report_path = os.environ["TRACELET_PILOT_REPORT_DIR"]
    from pathlib import Path

    backup = Path(report_path) / "knowledge-backup.json"
    backup.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    failed, first_inserted = False, False
    try:
        store.ensure_collection(recreate=True)
        # 第一条成功后，以错误维度制造半途失败；不是在线版本发布能力。
        store.insert(snapshot[:1])
        first_inserted = True
        store.insert([{**snapshot[0], "embedding": [0.0]}])
    except Exception:  # noqa: BLE001 -- 真实依赖故障演练，随后必须从快照恢复并重新计数。
        failed = first_inserted
    finally:
        saved = json.loads(backup.read_text(encoding="utf-8"))
        store.ensure_collection(recreate=True)
        store.insert(saved)
        redis.Redis.from_url(settings.redis_url).flushdb()
    client.load_collection(store.COLLECTION)
    restored = snapshot_rows(client, store.COLLECTION, fields, original)
    restored = [{key: ([float(value) for value in row[key]] if key == "embedding" else row[key])
                 for key in fields} for row in restored]
    canonical = lambda rows: sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)
    if not failed or canonical(restored) != canonical(snapshot):
        raise RuntimeError("维护恢复演练未达到断言")
    manifest = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
                for file in sorted(KNOWLEDGE_DIR.glob("*.md"))}
    (Path(report_path) / "knowledge-recovery.json").write_text(json.dumps({
        "injected_failure": "invalid_vector_dimension_after_first_insert",
        "restored_chunks": original, "sources": manifest,
        "collection": store.COLLECTION, "embedding_dim": settings.embedding_dim,
        "embedding_provider": settings.embedding_provider,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_rows(client, collection, fields, expected, timeout=60):
    """进程健康和 load 完成不代表分片已恢复；等到与 SQL 清单一致，绝不接受空快照。"""
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
    chunks = store.all_chunks()
    if len(chunks) != store.count():
        raise RuntimeError("向量存储隐私检查范围不完整")
    result = {"sql_hits": sql_hits, "redis_keys_scanned": redis_keys, "redis_hits": redis_hits,
              "milvus_chunks_scanned": len(chunks), "milvus_hits": sum(marker in str(row) for row in chunks)}
    (Path(os.environ["TRACELET_PILOT_REPORT_DIR"]) / "privacy.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    if any(sql_hits.values()) or redis_hits or result["milvus_hits"]:
        raise RuntimeError("合成敏感标记进入了持久化或缓存")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "knowledge-recovery", "ingest", "audit"))
    args = parser.parse_args()
    require_isolated()
    if args.action == "seed":
        seed()
    elif args.action == "knowledge-recovery":
        knowledge_recovery()
    elif args.action == "audit":
        audit()
    else:
        from app.rag.ingest import ingest_all

        asyncio.run(ingest_all(recreate=True))


if __name__ == "__main__":
    main()
