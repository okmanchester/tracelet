# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""调用日志查询工具（真实查询 MySQL）。"""

from datetime import timedelta

from sqlalchemy import func, select

from app import clock
from app.db import AsyncSessionLocal
from app.models import ApiCallLog, ApiKey
from app.tools.registry import ToolContext, ToolSpec, register


async def query_call_log(args: dict, ctx: ToolContext) -> dict:
    """按 request_id 查询单次调用日志。"""
    request_id = args.get("request_id")
    if not request_id:
        return {"found": False, "reason": "缺少 request_id"}
    async with AsyncSessionLocal() as s:
        log = (
            await s.execute(select(ApiCallLog).where(ApiCallLog.request_id == request_id))
        ).scalar_one_or_none()
        if log is None:
            return {"found": False, "reason": "未找到该 request_id 的日志"}
        # 租户隔离：客户侧只能查本租户
        if not ctx.is_internal and log.tenant_id != ctx.tenant_id:
            return {"found": False, "reason": "无权访问其它租户的调用日志"}
        key_masked = None
        if log.api_key_id:
            key = (
                await s.execute(select(ApiKey).where(ApiKey.id == log.api_key_id))
            ).scalar_one_or_none()
            key_masked = key.key_masked if key else None
        return {
            "found": True,
            "request_id": log.request_id,
            "app_id": log.app_id,
            "endpoint": log.endpoint,
            "http_status": log.http_status,
            "error_code": log.error_code,
            "latency_ms": log.latency_ms,
            "api_key_masked": key_masked,
            "called_at": log.created_at.isoformat(),
        }


async def query_recent_call_stats(args: dict, ctx: ToolContext) -> dict:
    """统计 (start, end]；默认截至当前 UTC，提供 request_id 时截至该历史事件。"""
    endpoint = args.get("endpoint")
    minutes = args.get("minutes", 60)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("minutes 必须为正整数")
    request_id = args.get("request_id")
    anchor = clock.now_utc()
    async with AsyncSessionLocal() as s:
        base_conds = [ApiCallLog.tenant_id == ctx.tenant_id]
        if endpoint:
            base_conds.append(ApiCallLog.endpoint == endpoint)
        if request_id is not None:
            # 历史窗口必须由明确的、本租户可见的事件决定，不隐式挑选最近 429。
            anchor = (
                await s.execute(select(ApiCallLog.created_at).where(
                    *base_conds, ApiCallLog.request_id == request_id,
                ))
            ).scalar()
            if anchor is None:
                return {"found": False, "reason": "未找到对应调用事件或无权访问"}
        anchor = clock.as_utc(anchor)
        try:
            since = anchor - timedelta(minutes=minutes)
        except OverflowError:
            raise ValueError("minutes 超出可用时间范围") from None
        conds = [*base_conds,
                 ApiCallLog.created_at > clock.storage_utc(since),
                 ApiCallLog.created_at <= clock.storage_utc(anchor)]
        rows = (
            await s.execute(
                select(ApiCallLog.http_status, func.count())
                .where(*conds)
                .group_by(ApiCallLog.http_status)
            )
        ).all()
        by_status = {int(st): int(cnt) for st, cnt in rows}
        total = sum(by_status.values())
        n429 = by_status.get(429, 0)
        return {
            "found": True,
            "endpoint": endpoint,
            "window_minutes": minutes,
            "window_mode": "historical" if request_id is not None else "realtime",
            "window_start": since.isoformat(),
            "window_end": anchor.isoformat(),
            "total": total,
            "by_status": by_status,
            "rate_limited_count": n429,
            "rate_limited_ratio": round(n429 / total, 3) if total else 0.0,
        }


register(ToolSpec(
    name="query_call_log",
    description="根据 request_id 查询某次 API 调用的状态码、错误码、耗时、调用时间等日志详情。",
    parameters={
        "type": "object",
        "properties": {"request_id": {"type": "string", "description": "调用请求 ID，如 req_20260615_8842"}},
        "required": ["request_id"],
    },
    func=query_call_log,
    category="logs",
))

register(ToolSpec(
    name="query_recent_call_stats",
    description="统计某接口截至当前 UTC 的近 N 分钟调用量；提供 request_id 则统计截至该历史调用事件的窗口 (start, end]。",
    parameters={
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "接口路径，如 /v1/risk/score"},
            "minutes": {"type": "integer", "minimum": 1, "description": "时间窗口（分钟），默认 60"},
            "request_id": {"type": "string", "description": "可选：以本租户该调用事件时间为窗口上界，不提供则使用当前 UTC"},
        },
    },
    func=query_recent_call_stats,
    category="logs",
))
