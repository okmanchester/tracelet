"""运行路径使用真实 UTC；测试/离线演示可替换 now_utc，不提供全局冻结配置。"""

from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """已有 MySQL DATETIME 由 UTC 写入；带时区输入先转换为同一时刻。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def storage_utc(value: datetime) -> datetime:
    """和现有无时区 UTC DATETIME 列比较，避免数据库按本机时区转换。"""
    return as_utc(value).replace(tzinfo=None)
