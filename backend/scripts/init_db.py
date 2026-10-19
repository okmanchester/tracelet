# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""初始化脚本：受控迁移 MySQL schema + 确保 Milvus collection 存在。

用法（在 backend 目录下）：
    python -m scripts.init_db                  # 升级受管 MySQL schema，确保 collection 存在
    python -m scripts.init_db --recreate --yes # 删除并重建受管表、版本表和 Milvus collection
"""

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="受控升级 MySQL schema 并确保集合存在；已有未登记库须先接管")
    parser.add_argument("--recreate", action="store_true", help="删除并重建受管表、版本表和集合（需 --yes）")
    parser.add_argument("--yes", action="store_true", help="确认删除已有数据")
    args = parser.parse_args(argv)
    if args.recreate and not args.yes:
        parser.error("--recreate 会删除受管 MySQL 表、版本表和 Milvus collection，必须同时指定 --yes")

    # 参数确认通过后再加载存储依赖，拒绝的请求不接触数据库或向量服务。
    from app.db import sync_engine
    from app.rag import store
    from app.schema_migrations import MigrationError, migrate

    print("[init_db] 检查并迁移 MySQL schema ...")
    try:
        print(migrate(sync_engine, "reset" if args.recreate else "upgrade", confirmed=args.yes))
    except MigrationError as exc:
        parser.exit(1, f"[init_db] {exc}\n")

    print("[init_db] 创建 Milvus collection ...")
    store.ensure_collection(recreate=args.recreate)
    print(f"[init_db] Milvus collection '{store.COLLECTION}' 就绪。")


if __name__ == "__main__":
    main()
