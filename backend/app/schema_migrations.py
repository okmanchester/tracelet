"""受控 MySQL 迁移：明确接管、结构检查和同连接管理锁（ADR-0001）。"""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from sqlalchemy import MetaData, inspect, text

from app.schema_compare import VERSION_TABLE, schema_diff
from migrations.baseline import metadata as baseline_metadata

BASELINE = "0001_initial_schema"
BACKEND = Path(__file__).resolve().parents[1]


class MigrationError(RuntimeError):
    pass


def current_metadata() -> MetaData:
    from app import models  # noqa: F401
    from app.db import Base

    return Base.metadata


def _configuration(connection, script_location):
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(script_location or BACKEND / "migrations"))
    config.attributes["connection"] = connection
    return config


def _history(config):
    from alembic.script import ScriptDirectory

    history = ScriptDirectory.from_config(config)
    heads = history.get_heads()
    revisions = list(history.walk_revisions())
    parents = [revision.down_revision for revision in revisions if revision.down_revision]
    if len(heads) != 1 or any(not isinstance(parent, str) for parent in parents):
        raise MigrationError("迁移历史必须只有一条版本链和一个 head")
    if len(parents) != len(set(parents)):
        raise MigrationError("不支持分支迁移历史")
    roots = [revision.revision for revision in revisions if revision.down_revision is None]
    if roots != [BASELINE]:
        raise MigrationError("迁移历史缺少受支持的固定初始版本")
    return heads[0], {revision.revision for revision in revisions}


def _revision(connection, known):
    if not inspect(connection).has_table(VERSION_TABLE):
        return None
    revisions = tuple(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if len(revisions) > 1 or any(revision not in known for revision in revisions):
        raise MigrationError("数据库版本未知或包含多条版本记录，拒绝自动修改")
    return revisions[0] if revisions else None


def _read_schema(connection) -> MetaData:
    inspector = inspect(connection)
    if inspector.get_view_names():
        raise MigrationError("存在未受管视图，须人工评审，拒绝自动修改")
    for category, schema_column in (("TRIGGERS", "TRIGGER_SCHEMA"), ("ROUTINES", "ROUTINE_SCHEMA")):
        count = connection.scalar(text(
            f"SELECT COUNT(*) FROM information_schema.{category} WHERE {schema_column}=DATABASE()"
        ))
        if count:
            raise MigrationError("存在未受管触发器或存储例程，须人工评审")
    actual = MetaData()
    actual.reflect(connection, only=lambda name, _: name != VERSION_TABLE)
    return actual


def _require_schema(connection, expected, actual=None):
    actual = actual if actual is not None else _read_schema(connection)
    differences = schema_diff(expected, actual, default_schema=connection.dialect.default_schema_name)
    if differences:
        raise MigrationError("结构检查失败：" + "；".join(differences))


@contextmanager
def _management_lock(connection, database):
    name = "tracelet:migrate:" + sha256(database.encode("utf-8")).hexdigest()[:32]
    if connection.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name}) != 1:
        raise MigrationError("无法取得数据库迁移锁，可能已有管理操作正在执行")
    try:
        yield
    finally:
        # 清除可能尚未结束的事务；这不能回滚已生效的 MySQL DDL。
        try:
            connection.rollback()
            released = connection.scalar(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
            if released != 1:
                raise MigrationError("数据库迁移锁释放失败")
            connection.commit()
        except Exception:  # noqa: BLE001 - 锁释放边界必须作废物理连接，不能泄漏锁
            # 不能将可能仍持有 advisory lock 的物理连接还给连接池。
            connection.invalidate()
            raise MigrationError("迁移锁释放失败，连接已作废；请核查数据库状态") from None


def _perform(connection, operation, script_location):
    from alembic import command

    config = _configuration(connection, script_location)
    head, known = _history(config)
    revision = _revision(connection, known)
    if operation == "current":
        return f"当前版本：{revision or '未登记'}；目标版本：{head}"
    actual = _read_schema(connection)
    if operation == "check":
        if revision is None:
            _require_schema(connection, baseline_metadata(), actual)
            raise MigrationError("结构匹配固定基线，但版本未登记；请显式执行 adopt-baseline --yes")
        if revision != head:
            raise MigrationError("数据库尚未达到 head，请检查升级步骤")
        _require_schema(connection, current_metadata(), actual)
        return f"结构与当前模型一致，版本：{head}"
    if operation == "adopt-baseline":
        if revision not in (None, BASELINE):
            raise MigrationError("只能接管未登记库或检查已经登记的初始版本，不能回退版本")
        _require_schema(connection, baseline_metadata(), actual)
        if revision is None:
            connection.commit()
            command.stamp(config, BASELINE)
            connection.commit()
        return f"初始版本已登记：{BASELINE}；业务记录未修改"
    if operation == "reset":
        allowed = set(baseline_metadata().tables) | set(current_metadata().tables)
        if set(actual.tables) - allowed:
            raise MigrationError("数据库存在未受管表，拒绝自动重置")
        # 包括版本表；不能留下旧版本后让 upgrade 错误地跳过建表。
        reset_metadata = MetaData()
        reset_metadata.reflect(connection)
        reset_metadata.drop_all(connection)
        connection.commit()
    elif revision is None:
        if actual.tables:
            raise MigrationError("非空数据库尚未接管，请先执行 adopt-baseline --yes；不会自动建表或标记版本")
    elif revision == head:
        _require_schema(connection, current_metadata(), actual)
        return f"数据库已在目标版本：{head}"
    elif revision == BASELINE:
        _require_schema(connection, baseline_metadata(), actual)

    # 反射查询会开启隐式事务，必须先结束，交由 Alembic 提交版本记录。
    connection.commit()
    command.upgrade(config, "head")
    connection.commit()
    _require_schema(connection, current_metadata())
    return f"数据库迁移完成：{head}"


def migrate(engine, operation="upgrade", *, confirmed=False, script_location=None) -> str:
    if operation not in {"current", "check", "upgrade", "adopt-baseline", "reset"}:
        raise MigrationError("未知迁移操作")
    if operation in {"adopt-baseline", "reset"} and not confirmed:
        raise MigrationError("接管或重置必须显式确认 --yes")
    try:
        # 缺依赖时先拒绝，不能先连接数据库再报告安装问题。
        import alembic  # noqa: F401

        with engine.connect() as connection:
            if connection.dialect.name != "mysql" or getattr(connection.dialect, "is_mariadb", False):
                raise MigrationError("受控迁移仅支持 MySQL")
            database = connection.scalar(text("SELECT DATABASE()"))
            if not database:
                raise MigrationError("必须显式指定目标数据库")
            if operation in {"current", "check"}:
                return _perform(connection, operation, script_location)
            with _management_lock(connection, database):
                return _perform(connection, operation, script_location)
    except MigrationError:
        raise
    except ImportError:
        raise MigrationError("缺少迁移依赖，请在 tracelet 环境重新安装后端依赖") from None
    except Exception as exc:  # noqa: BLE001 - 对管理入口统一隐藏可能带凭证的原始异常
        # 原始异常可能带连接凭证；CLI 不打印其内容，也不声称 DDL 会整批回滚。
        raise MigrationError(
            f"迁移失败（{type(exc).__name__}）；DDL 可能已部分生效，请核查版本和结构后恢复，勿盲目重试"
        ) from None
