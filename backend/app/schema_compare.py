"""MySQL 结构比较：不依赖当前 ORM，不执行 DDL，也不按约束名称判断兼容。"""

import re

from sqlalchemy import Boolean, MetaData, Table
from sqlalchemy.dialects import mysql

VERSION_TABLE = "alembic_version"


def _type_signature(column) -> str:
    value = column.type
    compiled = value.compile(dialect=mysql.dialect()).upper()
    if isinstance(value, Boolean) or compiled == "TINYINT(1)":
        return "BOOLEAN"
    # MySQL 反射出的整数显示宽度不是取值精度；unsigned/zerofill 仍保留比较。
    compiled = re.sub(r"\b(TINYINT|SMALLINT|MEDIUMINT|INTEGER|INT|BIGINT)\(\d+\)", r"\1", compiled)
    return re.sub(r"\bINTEGER\b", "INT", compiled)


def _default_signature(column):
    if column.computed is not None:
        return ("computed", str(column.computed.sqltext))
    if column.identity is not None:
        return ("identity",)
    if column.server_default is None:
        return None
    value = str(column.server_default.arg).strip()
    return None if value.upper() == "NULL" else value


def _index_signature(index):
    options = index.dialect_options["mysql"]
    length = options.get("length")
    if isinstance(length, dict):
        length = tuple(sorted(length.items()))
    return (
        tuple(column.name for column in index.columns),
        length,
        (options.get("using") or "BTREE").upper(),
        (options.get("prefix") or "").upper(),
    )


def _action(value):
    return "RESTRICT" if value is None or value.upper() in {"RESTRICT", "NO ACTION"} else value.upper()


def _table_signature(table: Table, default_schema: str | None):
    columns = {
        column.name: (
            _type_signature(column), column.nullable, _default_signature(column),
            column.autoincrement is True, column.computed is not None, column.identity is not None,
        )
        for column in table.columns
    }
    primary = tuple(column.name for column in table.primary_key.columns)
    unique = set()
    regular = set()
    for index in table.indexes:
        signature = _index_signature(index)
        (unique if index.unique else regular).add(signature)
    from sqlalchemy import UniqueConstraint

    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            unique.add((tuple(column.name for column in constraint.columns), None, "BTREE", ""))
    foreign = set()
    for constraint in table.foreign_key_constraints:
        elements = list(constraint.elements)
        remote = elements[0].column.table
        schema = None if remote.schema in (None, default_schema) else remote.schema
        local_columns = tuple(element.parent.name for element in elements)
        foreign.add((
            local_columns, schema, remote.name, tuple(element.column.name for element in elements),
            _action(constraint.ondelete), _action(constraint.onupdate), constraint.match,
            constraint.deferrable, constraint.initially,
        ))
        # MySQL 自动为未被其他索引前缀覆盖的外键建立支持索引。
        indexed = [primary, *(entry[0] for entry in unique | regular if entry[1] is None)]
        if not any(value[:len(local_columns)] == local_columns for value in indexed):
            regular.add((local_columns, None, "BTREE", ""))
    return {"columns": columns, "primary_key": primary, "unique": unique, "indexes": regular, "foreign_keys": foreign}


def schema_diff(expected: MetaData, actual: MetaData, *, default_schema: str | None = None) -> list[str]:
    """返回可定位的结构差异；不输出数据或连接凭证。"""
    wanted = {table.name: table for table in expected.tables.values() if table.name != VERSION_TABLE}
    found = {table.name: table for table in actual.tables.values() if table.name != VERSION_TABLE}
    differences = []
    for name in sorted(wanted.keys() - found.keys()):
        differences.append(f"缺少表 {name}")
    for name in sorted(found.keys() - wanted.keys()):
        differences.append(f"额外表 {name}")
    for name in sorted(wanted.keys() & found.keys()):
        left = _table_signature(wanted[name], default_schema)
        right = _table_signature(found[name], default_schema)
        for column in sorted(left["columns"].keys() | right["columns"].keys()):
            if left["columns"].get(column) != right["columns"].get(column):
                differences.append(f"列不符 {name}.{column}")
        for key in ("primary_key", "unique", "indexes", "foreign_keys"):
            if left[key] != right[key]:
                differences.append(f"约束或索引不符 {name}.{key}")
    return differences
