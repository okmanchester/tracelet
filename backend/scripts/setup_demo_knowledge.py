"""Explicit demo-only build/validate/publication; normal ingest never publishes."""

import argparse
import asyncio
from uuid import uuid4


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true")
    if not parser.parse_args(argv).yes:
        parser.error("演示知识发布必须指定 --yes")
    from app import knowledge
    from app.db import SyncSessionLocal
    from app.models import KnowledgeHead
    # Capture expected generation before doing expensive work; do not overwrite a concurrent publication.
    with SyncSessionLocal() as db:
        head = db.get(KnowledgeHead, 1)
        if head is None:
            parser.error("请先运行受控迁移")
        generation = head.generation
    result = asyncio.run(knowledge.build())
    knowledge.validate(result["knowledge_version"])
    publication = knowledge.publish(result["knowledge_version"], generation, "setup-demo", uuid4())
    print("[setup-demo] 已显式发布知识版本 " + publication["knowledge_version"])


if __name__ == "__main__":
    main()
