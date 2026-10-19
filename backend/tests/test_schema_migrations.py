"""固定基线、结构归一化和受控迁移状态机测试；不连接真实数据库。"""

import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.dialects import mysql

from app import schema_migrations as migration
from app.schema_compare import schema_diff
from migrations.baseline import metadata
from scripts import migrate_db


def test_frozen_baseline_matches_current_models_and_preserves_microseconds():
    frozen = metadata()
    assert len(frozen.tables) == 19
    assert not schema_diff(frozen, migration.current_metadata())
    assert frozen.tables["message"].c.created_at.type.fsp == 6


def test_baseline_generates_offline_mysql_sql_without_current_model_ddl(monkeypatch):
    import io

    config = migration._configuration(None, None)
    config.output_buffer = io.StringIO()
    monkeypatch.setattr(migration.current_metadata(), "create_all", Mock(side_effect=AssertionError("ORM DDL")))
    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue()
    assert sql.count("CREATE TABLE") == 20  # 19 张业务表加版本表。
    assert "DATETIME(6)" in sql
    assert "DROP TABLE" not in sql
    assert migration.BASELINE in sql


def test_mysql_reflection_differences_are_compared_semantically():
    actual = metadata()
    actual.tables["conversation"].c.resolved_by_ai.type = mysql.TINYINT(display_width=1)
    actual.tables["usage_record"].c.id.type = mysql.BIGINT(display_width=20)
    actual.tables["api_call_log"].c.http_status.type = mysql.INTEGER(display_width=11)
    sa.Index("mysql_generated_fk_index", actual.tables["tenant"].c.plan_id)
    for table in actual.tables.values():
        for index in table.indexes:
            index.name = "renamed_" + index.name
    assert not schema_diff(metadata(), actual)


@pytest.mark.parametrize("change, expected", [
    ("microseconds", "message.created_at"),
    ("nullable", "message.content"),
    ("default", "message.content"),
    ("extra_column", "message.unmanaged"),
    ("missing_table", "api_key"),
    ("extra_table", "unmanaged"),
    ("unique", "user.unique"),
    ("foreign", "message.foreign_keys"),
    ("primary", "message.primary_key"),
    ("unsigned", "usage_record.id"),
])
def test_schema_drift_is_not_silently_accepted(change, expected):
    actual = metadata()
    table = actual.tables["message"]
    if change == "microseconds":
        table.c.created_at.type = mysql.DATETIME(fsp=3)
    elif change == "nullable":
        table.c.content.nullable = True
    elif change == "default":
        table.c.content.server_default = sa.DefaultClause("unexpected")
    elif change == "extra_column":
        table.append_column(sa.Column("unmanaged", sa.String(8)))
    elif change == "missing_table":
        actual.remove(actual.tables["api_key"])
    elif change == "extra_table":
        sa.Table("unmanaged", actual, sa.Column("id", sa.Integer, primary_key=True))
    elif change == "unique":
        next(index for index in actual.tables["user"].indexes if index.unique).unique = False
    elif change == "foreign":
        constraint = next(iter(table.foreign_key_constraints))
        constraint.ondelete = "CASCADE"
    elif change == "primary":
        table.c.id.primary_key = False
        table.primary_key = sa.PrimaryKeyConstraint(table.c.conversation_id)
    elif change == "unsigned":
        actual.tables["usage_record"].c.id.type = mysql.BIGINT(unsigned=True)
    assert any(expected in difference for difference in schema_diff(metadata(), actual))


@pytest.fixture
def state(monkeypatch):
    from types import SimpleNamespace

    value = SimpleNamespace(revision=None, actual=metadata(), connection=Mock())
    value.connection.dialect.default_schema_name = "test"
    monkeypatch.setattr(migration, "_configuration", lambda *args: object())
    monkeypatch.setattr(migration, "_history", lambda config: (migration.BASELINE, {migration.BASELINE}))
    monkeypatch.setattr(migration, "_revision", lambda connection, known: value.revision)
    monkeypatch.setattr(migration, "_read_schema", lambda connection: value.actual)
    monkeypatch.setattr(command, "upgrade", Mock())
    monkeypatch.setattr(command, "stamp", Mock())
    return value


def test_unversioned_nonempty_database_is_refused_without_ddl_or_stamp(state):
    with pytest.raises(migration.MigrationError, match="非空数据库尚未接管"):
        migration._perform(state.connection, "upgrade", None)
    command.upgrade.assert_not_called()
    command.stamp.assert_not_called()


def test_matching_legacy_database_is_stamped_only(state):
    migration._perform(state.connection, "adopt-baseline", None)
    command.stamp.assert_called_once()
    assert command.stamp.call_args.args[1] == migration.BASELINE
    command.upgrade.assert_not_called()


def test_adoption_rejects_drift_before_stamp(state):
    state.actual.tables["message"].c.created_at.type = mysql.DATETIME(fsp=0)
    with pytest.raises(migration.MigrationError, match="message.created_at"):
        migration._perform(state.connection, "adopt-baseline", None)
    command.stamp.assert_not_called()


def test_repeated_baseline_adoption_does_not_write_version_again(state):
    state.revision = migration.BASELINE
    migration._perform(state.connection, "adopt-baseline", None)
    command.stamp.assert_not_called()


def test_repeated_upgrade_at_head_is_non_destructive(state):
    state.revision = migration.BASELINE
    migration._perform(state.connection, "upgrade", None)
    command.upgrade.assert_not_called()
    command.stamp.assert_not_called()


def test_empty_database_runs_migration_history(state, monkeypatch):
    state.actual = sa.MetaData()
    monkeypatch.setattr(migration, "_require_schema", Mock())
    migration._perform(state.connection, "upgrade", None)
    command.upgrade.assert_called_once()
    assert command.upgrade.call_args.args[1] == "head"
    command.stamp.assert_not_called()


@pytest.mark.parametrize("operation", ["adopt-baseline", "reset"])
def test_unconfirmed_write_is_rejected_before_database_connection(operation):
    engine = Mock()
    with pytest.raises(migration.MigrationError, match="显式确认"):
        migration.migrate(engine, operation)
    engine.connect.assert_not_called()


def test_unconfirmed_cli_adoption_does_not_load_database(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "app.db":
            pytest.fail("未确认接管时不应加载数据库配置")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(SystemExit) as error:
        migrate_db.main(["adopt-baseline"])
    assert error.value.code == 2


@pytest.mark.parametrize("revisions", [("future",), (migration.BASELINE, migration.BASELINE)])
def test_unknown_or_multiple_database_versions_are_refused(monkeypatch, revisions):
    connection = Mock()
    inspector = Mock()
    inspector.has_table.return_value = True
    monkeypatch.setattr(migration, "inspect", lambda connection: inspector)
    connection.execute.return_value.scalars.return_value = revisions
    with pytest.raises(migration.MigrationError, match="版本未知"):
        migration._revision(connection, {migration.BASELINE})


def test_management_lock_is_released_on_body_failure():
    connection = Mock()
    connection.scalar.side_effect = [1, 1]
    with pytest.raises(RuntimeError, match="synthetic"), migration._management_lock(connection, "test"):
        raise RuntimeError("synthetic")
    assert "RELEASE_LOCK" in str(connection.scalar.call_args.args[0])
    connection.invalidate.assert_not_called()


def test_busy_management_lock_does_not_enter_operation():
    connection = Mock()
    connection.scalar.return_value = 0
    body = Mock()
    with pytest.raises(migration.MigrationError, match="无法取得"), migration._management_lock(connection, "test"):
        body()
    body.assert_not_called()


def test_failed_unlock_invalidates_physical_connection():
    connection = Mock()
    connection.scalar.side_effect = [1, 0]
    with pytest.raises(migration.MigrationError, match="连接已作废"), migration._management_lock(connection, "test"):
        pass
    connection.invalidate.assert_called_once()


def test_real_history_is_linear_and_downgrade_is_not_silent():
    assert migration._history(migration._configuration(None, None)) == (migration.BASELINE, {migration.BASELINE})
    revision = importlib.import_module("migrations.versions.0001_initial_schema")
    with pytest.raises(RuntimeError, match="删除业务数据"):
        revision.downgrade()


def test_parallel_heads_are_refused(tmp_path):
    directory = tmp_path / "versions"
    directory.mkdir()
    for identifier, parent in ((migration.BASELINE, None), ("branch_a", migration.BASELINE), ("branch_b", migration.BASELINE)):
        (directory / f"{identifier}.py").write_text(
            f"revision={identifier!r}\ndown_revision={parent!r}\n", encoding="utf-8",
        )
    with pytest.raises(migration.MigrationError, match="一条版本链"):
        migration._history(migration._configuration(None, Path(tmp_path)))


def test_raw_connection_error_does_not_expose_credentials():
    engine = Mock()
    engine.connect.side_effect = RuntimeError("mysql+pymysql://user:synthetic_secret@server/db")
    with pytest.raises(migration.MigrationError) as error:
        migration.migrate(engine)
    assert "synthetic_secret" not in str(error.value)
    assert "RuntimeError" in str(error.value)


def test_readonly_legacy_check_reports_compatibility_without_stamp(state):
    with pytest.raises(migration.MigrationError, match="结构匹配固定基线"):
        migration._perform(state.connection, "check", None)
    command.stamp.assert_not_called()
    command.upgrade.assert_not_called()


def test_reset_does_not_drop_unmanaged_tables(state, monkeypatch):
    sa.Table("unmanaged", state.actual, sa.Column("id", sa.Integer))
    reflect = Mock(side_effect=AssertionError("reset DDL must not start"))
    monkeypatch.setattr(sa.MetaData, "reflect", reflect)
    with pytest.raises(migration.MigrationError, match="未受管表"):
        migration._perform(state.connection, "reset", None)
    reflect.assert_not_called()
    command.upgrade.assert_not_called()


@pytest.mark.parametrize("views, routines", [(["unmanaged_view"], 0), ([], 1)])
def test_unsupported_objects_are_refused_before_reflection(monkeypatch, views, routines):
    inspector = Mock()
    inspector.get_view_names.return_value = views
    monkeypatch.setattr(migration, "inspect", lambda connection: inspector)
    connection = Mock()
    connection.scalar.return_value = routines
    with pytest.raises(migration.MigrationError, match="未受管"):
        migration._read_schema(connection)
