"""评估和压测的临时执行限制；不代表依赖已经隔离。"""

from app.config import settings


class OfflineRunDisabled(RuntimeError):
    """当前环境禁止执行可能污染业务数据的离线任务。"""


def offline_runs_allowed() -> bool:
    # 未知环境也拒绝，避免新增部署环境时遗漏限制。
    return settings.app_env.strip().casefold() in {"dev", "test", "eval"}


def require_offline_run() -> None:
    if not offline_runs_allowed():
        raise OfflineRunDisabled("当前环境禁止运行评估和压测，请使用独立评估环境")
