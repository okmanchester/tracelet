"""附属任务管理：list [--operation ID]、retry TASK、drain；仅 retry/drain 执行变更。"""

import argparse
import asyncio
import json

from sqlalchemy import select

from app.db import AsyncSessionLocal
from app.effects import drain_once
from app.models import EffectTask
from app.operations import db_now


async def run(args):
    if args.command == "drain":
        count = 0
        while count < args.limit and await drain_once():
            count += 1
        print(json.dumps({"processed": count}))
        return
    async with AsyncSessionLocal() as db:
        if args.command == "retry":
            task = (await db.execute(select(EffectTask).where(EffectTask.id == args.task_id)
                                    .with_for_update())).scalar_one_or_none()
            if task is None or task.status != "failed":
                raise ValueError("任务不存在或不是最终失败状态")
            task.status, task.attempts, task.error = "pending", 0, None
            task.available_at = await db_now(db)
            await db.commit()
            print(json.dumps({"retried": task.id}))
        else:
            stmt = select(EffectTask)
            if args.operation:
                stmt = stmt.where(EffectTask.operation_id == args.operation.removeprefix("op_"))
            rows = (await db.execute(stmt.order_by(EffectTask.available_at).limit(100))).scalars().all()
            print(json.dumps([{"id": t.id, "operation_id": t.operation_id, "kind": t.kind,
                               "status": t.status, "attempts": t.attempts, "error": t.error}
                              for t in rows], ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list").add_argument("--operation")
    commands.add_parser("retry").add_argument("task_id")
    commands.add_parser("drain").add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.command == "drain" and args.limit <= 0:
        parser.error("limit 必须为正整数")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
