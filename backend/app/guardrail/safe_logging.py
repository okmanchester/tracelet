"""在格式化之前移除日志正文和异常中的敏感内容。"""

import logging

from app.guardrail import desensitize


def install_safe_logging() -> None:
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_tracelet_safe", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg = desensitize.desensitize_text(record.getMessage())
        except Exception:  # noqa: BLE001 -- 日志失败时也不得格式化原文。
            record.msg = "日志内容安全处理失败"
        record.args = ()
        if record.exc_info or record.exc_text:
            record.msg += " [异常详情已省略]"
            record.exc_info = None
            record.exc_text = None
        return record

    factory._tracelet_safe = True
    logging.setLogRecordFactory(factory)
