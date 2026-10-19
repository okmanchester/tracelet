"""可选的真实 MySQL 迁移测试；不会连接应用配置库。"""

import shutil

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import models
from app import schema_migrations as migration
from app.db import Base
from migrations.baseline import metadata


def seed_and_snapshot(engine):
    with Session(engine) as session:
        session.add(models.Conversation(id="keep", tenant_id="synthetic", user_id="synthetic"))
        session.flush()
        session.add(models.Message(id="keep_msg", conversation_id="keep", role="user", content="keep"))
        session.commit()
    return snapshot(engine)


def snapshot(engine):
    with engine.connect() as connection:
        return {
            name: list(connection.execute(sa.select(Base.metadata.tables[name])).mappings())
            for name in ("conversation", "message")
        }


def version(engine):
    with engine.connect() as connection:
        return migration._revision(connection, {migration.BASELINE, "0002_trial"})


def test_legacy_adoption_and_repeated_upgrade_preserve_data(mysql_engine):
    Base.metadata.create_all(mysql_engine)
    before = seed_and_snapshot(mysql_engine)
    with pytest.raises(migration.MigrationError, match="非空数据库尚未接管"):
        migration.migrate(mysql_engine)
    assert not sa.inspect(mysql_engine).has_table("alembic_version")
    migration.migrate(mysql_engine, "adopt-baseline", confirmed=True)
    migration.migrate(mysql_engine, "adopt-baseline", confirmed=True)
    migration.migrate(mysql_engine)
    migration.migrate(mysql_engine, "check")
    assert version(mysql_engine) == migration.BASELINE
    assert snapshot(mysql_engine) == before


@pytest.mark.parametrize("ddl", [
    "ALTER TABLE message DROP COLUMN content",
    "ALTER TABLE message MODIFY COLUMN created_at DATETIME(3) NOT NULL",
    "ALTER TABLE message ADD COLUMN unmanaged INT NULL",
    "ALTER TABLE user DROP INDEX ix_user_username",
    "CREATE TABLE unmanaged (id INT PRIMARY KEY)",
])
def test_legacy_drift_is_refused_without_version_or_repair(mysql_engine, ddl):
    Base.metadata.create_all(mysql_engine)
    with mysql_engine.begin() as connection:
        connection.execute(sa.text(ddl))
    before = sa.MetaData()
    before.reflect(mysql_engine)
    with pytest.raises(migration.MigrationError, match="结构检查失败"):
        migration.migrate(mysql_engine, "adopt-baseline", confirmed=True)
    assert not sa.inspect(mysql_engine).has_table("alembic_version")
    after = sa.MetaData()
    after.reflect(mysql_engine)
    from app.schema_compare import schema_diff

    assert not schema_diff(before, after)


def test_unknown_database_revision_is_refused(mysql_engine):
    migration.migrate(mysql_engine)
    with mysql_engine.begin() as connection:
        connection.execute(sa.text("UPDATE alembic_version SET version_num='future_version'"))
    before = seed_and_snapshot(mysql_engine)
    with pytest.raises(migration.MigrationError, match="版本未知"):
        migration.migrate(mysql_engine)
    assert snapshot(mysql_engine) == before


def test_read_commands_do_not_execute_writes(mysql_engine):
    migration.migrate(mysql_engine)
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    sa.event.listen(mysql_engine, "before_cursor_execute", observe)
    try:
        migration.migrate(mysql_engine, "current")
        migration.migrate(mysql_engine, "check")
    finally:
        sa.event.remove(mysql_engine, "before_cursor_execute", observe)
    assert not set(statements) & {"CREATE", "DROP", "ALTER", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REPLACE"}


def test_explicit_reset_clears_version_and_rebuilds(mysql_engine):
    migration.migrate(mysql_engine)
    seed_and_snapshot(mysql_engine)
    with pytest.raises(migration.MigrationError, match="显式确认"):
        migration.migrate(mysql_engine, "reset")
    migration.migrate(mysql_engine, "reset", confirmed=True)
    migration.migrate(mysql_engine, "check")
    assert version(mysql_engine) == migration.BASELINE
    assert not snapshot(mysql_engine)["conversation"]
    assert not snapshot(mysql_engine)["message"]


def test_concurrent_management_is_refused_and_lock_is_released(mysql_engine):
    with mysql_engine.connect() as connection:
        database = connection.scalar(sa.text("SELECT DATABASE()"))
        with migration._management_lock(connection, database), pytest.raises(migration.MigrationError, match="无法取得"):
            migration.migrate(mysql_engine)
    migration.migrate(mysql_engine)
    assert version(mysql_engine) == migration.BASELINE


@pytest.mark.parametrize("fail_midway", [False, True])
def test_incremental_revision_preserves_rows_and_exposes_nontransactional_failure(
    mysql_engine, tmp_path, monkeypatch, fail_midway,
):
    migration.migrate(mysql_engine)
    before = seed_and_snapshot(mysql_engine)
    directory = tmp_path / "migrations"
    shutil.copytree(migration.BACKEND / "migrations", directory, ignore=shutil.ignore_patterns("__pycache__"))
    source = (
        "from alembic import op\nimport sqlalchemy as sa\n"
        "revision='0002_trial'\ndown_revision='0001_initial_schema'\n"
        "def upgrade():\n"
        "    op.add_column('conversation', sa.Column('trial_note', sa.String(32), nullable=True))\n"
    )
    if fail_midway:
        source += "    raise RuntimeError('synthetic failure after DDL')\n"
    (directory / "versions" / "0002_trial.py").write_text(source, encoding="utf-8")
    expected = metadata()
    expected.tables["conversation"].append_column(sa.Column("trial_note", sa.String(32), nullable=True))
    monkeypatch.setattr(migration, "current_metadata", lambda: expected)
    if fail_midway:
        with pytest.raises(migration.MigrationError, match="DDL 可能已部分生效"):
            migration.migrate(mysql_engine, script_location=directory)
        assert version(mysql_engine) == migration.BASELINE
        assert "trial_note" in {column["name"] for column in sa.inspect(mysql_engine).get_columns("conversation")}
    else:
        migration.migrate(mysql_engine, script_location=directory)
        migration.migrate(mysql_engine, script_location=directory)
        assert version(mysql_engine) == "0002_trial"
    assert snapshot(mysql_engine) == before
