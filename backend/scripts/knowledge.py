"""Explicit knowledge build, validation, publication, rollback and result queries."""

import argparse
import asyncio
import json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    validate = commands.add_parser("validate")
    validate.add_argument("version")
    validate.add_argument("--timeout", type=float, default=60)
    for action in ("publish", "rollback"):
        command = commands.add_parser(action)
        command.add_argument("version")
        command.add_argument("--expected-generation", required=True, type=int)
        command.add_argument("--operator", required=True)
        command.add_argument("--publication-id", required=True)
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("version", nargs="?")
    show.add_argument("--publication-id")
    args = parser.parse_args(argv)
    from sqlalchemy import select

    from app import knowledge
    from app.db import SyncSessionLocal
    from app.models import KnowledgeHead, KnowledgeRelease
    try:
        if args.command == "build":
            result = asyncio.run(knowledge.build())
        elif args.command == "validate":
            if args.timeout <= 0:
                parser.error("timeout 必须为正数")
            result = knowledge.validate(args.version, args.timeout)
        elif args.command in {"publish", "rollback"}:
            result = knowledge.publish(args.version, args.expected_generation, args.operator,
                                       args.publication_id, action=args.command)
        elif args.command == "show" and args.publication_id:
            result = knowledge.publication(args.publication_id)
            if result is None:
                raise knowledge.KnowledgeError("knowledge_publication_not_found")
        else:
            with SyncSessionLocal() as db:
                head = db.get(KnowledgeHead, 1)
                versions = list(db.scalars(select(KnowledgeRelease).order_by(KnowledgeRelease.created_at)))
                if args.command == "show":
                    versions = [r for r in versions if r.id == args.version]
                    if not versions:
                        raise knowledge.KnowledgeError("knowledge_version_not_found")
                result = {"knowledge_version": head.release_id if head else None,
                          "generation": head.generation if head else 0,
                          "versions": [{"knowledge_version": r.id, "status": r.status,
                                        "manifest": r.manifest, "validation": r.validation, "error": r.error} for r in versions]}
        print(json.dumps(result, ensure_ascii=False))
    except knowledge.KnowledgeError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:  # noqa: BLE001 -- CLI 失败不能回显 SDK/数据库的原始敏感错误。
        parser.exit(1, "知识操作失败，请查询原版本或发布标识；未确认前不能视为已发布。\n")


if __name__ == "__main__":
    main()
