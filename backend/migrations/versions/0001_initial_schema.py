"""冻结现有 19 张业务表的初始结构；不读取当前 ORM 生成历史 DDL。"""

from alembic import op

from migrations.baseline import definitions, indexes, metadata

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = dict(definitions())
    for table in metadata().sorted_tables:
        op.create_table(table.name, *tables[table.name])
    for name, table, columns, unique in indexes():
        op.create_index(name, table, columns, unique=unique)


def downgrade() -> None:
    raise RuntimeError("初始版本降级会删除业务数据；请使用明确确认的 reset-db 入口")
