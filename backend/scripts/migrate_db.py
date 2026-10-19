"""MySQL 迁移管理：current / check / upgrade / adopt-baseline --yes。"""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="operation", required=True)
    for name in ("current", "check", "upgrade"):
        actions.add_parser(name)
    adopt = actions.add_parser("adopt-baseline", help="结构校验后登记固定初始版本，不执行业务 DDL")
    adopt.add_argument("--yes", action="store_true", help="确认接管已有库，须已备份并完成副本检查")
    args = parser.parse_args(argv)
    if args.operation == "adopt-baseline" and not args.yes:
        parser.error("接管必须显式指定 --yes；请先核对目标配置并在副本验证")

    from app.schema_migrations import MigrationError, migrate

    try:
        from app.db import sync_engine

        print(migrate(sync_engine, args.operation, confirmed=getattr(args, "yes", False)))
        return 0
    except MigrationError as exc:
        print(f"[migrate_db] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
