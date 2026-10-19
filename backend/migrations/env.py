"""线上迁移仅使用受控入口提供的连接，避免绕过接管和互斥检查。"""

from alembic import context

from app import models  # noqa: F401
from app.db import Base

if context.is_offline_mode():
    context.configure(dialect_name="mysql", target_metadata=Base.metadata, literal_binds=True)
else:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("请使用 python -m scripts.migrate_db，不能绕过结构检查直接在线迁移")
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)

with context.begin_transaction():
    context.run_migrations()
