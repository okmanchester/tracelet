# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""工具调用中心：注册、schema、超时、重试、脱敏日志、高风险隔离。

- 每个工具声明 input schema、超时、重试、是否高风险。
- execute() 真实执行并写 tool_call_log（参数与结果脱敏）。
- 高风险工具不暴露给 AI（openai_tools 默认排除），只能人工/后台执行。
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.access import INTERNAL_ROLES, require_identity
from app.config import settings
from app.db import AsyncSessionLocal
from app.guardrail import desensitize
from app.models import ToolCallLog

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    tenant_id: str
    trace_id: str = ""
    is_internal: bool = False
    user_id: str = ""
    role: str = ""
    operation_id: str = ""

    def __post_init__(self):
        self.is_internal = self.role in INTERNAL_ROLES and bool(self.user_id and self.tenant_id)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema
    func: Callable[[dict, ToolContext], Awaitable[dict]]
    timeout: float = settings.tool_timeout_seconds
    retries: int = 1
    high_risk: bool = False
    category: str = "general"
    mutating: bool = False
    idempotent: bool = False


REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    REGISTRY[spec.name] = spec
    return spec


def openai_tools(include_high_risk: bool = False) -> list[dict]:
    """返回可供 LLM function calling 的工具 schema（默认排除高风险）。"""
    tools = []
    for spec in REGISTRY.values():
        if spec.high_risk and not include_high_risk:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
        )
    return tools


async def _log_call(ctx, name, args, result, status, duration_ms, error):
    async with AsyncSessionLocal() as s:
        s.add(
            ToolCallLog(
                trace_id=ctx.trace_id,
                tenant_id=ctx.tenant_id,
                tool_name=name,
                args_summary=json.dumps(desensitize.desensitize_obj(args), ensure_ascii=False)[:1000],
                result_summary=json.dumps(desensitize.desensitize_obj(result), ensure_ascii=False)[:1500],
                status=status,
                duration_ms=duration_ms,
                error_message=desensitize.desensitize_obj(error),
            )
        )
        await s.commit()


async def _record_call(ctx, name, args, result, status, duration_ms, error):
    """附属日志异常不能触发业务重放或改变工具执行结果。"""
    try:
        await _log_call(ctx, name, args, result, status, duration_ms, error)
    except Exception:  # noqa: BLE001
        # 不包含异常载荷、参数、结果或 traceback，避免泄露原文。
        logger.warning("工具调用日志保存失败")


async def execute(name: str, args: dict, ctx: ToolContext) -> dict:
    """执行工具：高风险拦截 + 超时 + 重试 + 脱敏日志。"""
    require_identity(ctx)
    args = desensitize.desensitize_obj(args)
    spec = REGISTRY.get(name)
    if spec is None:
        return {"ok": False, "error": "未知工具"}

    # 高风险工具不允许 AI 直接执行（仅内部显式调用且需标记）
    if spec.high_risk and not ctx.is_internal:
        return {"ok": False, "error": f"工具 {name} 为高风险操作，需人工处理，AI 不可直接执行"}

    start = time.perf_counter()
    last_err = None
    last_status = "failed"
    retries = spec.retries if not spec.mutating or spec.idempotent else 0
    for attempt in range(retries + 1):
        try:
            data = await asyncio.wait_for(spec.func(args, ctx), timeout=spec.timeout)
            data = desensitize.desensitize_obj(data)
        except asyncio.TimeoutError:
            last_err = f"工具调用超时(>{spec.timeout}s)"
            last_status = "unknown" if spec.mutating else "failed"
        except desensitize.SensitiveDataError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = "工具执行失败"
            last_status = "unknown" if spec.mutating and not isinstance(exc, ValueError) else "failed"
        else:
            duration = int((time.perf_counter() - start) * 1000)
            await _record_call(ctx, name, args, data, "ok", duration, None)
            return {"ok": True, "data": data}
    duration = int((time.perf_counter() - start) * 1000)
    await _record_call(ctx, name, args, {}, "error", duration, last_err)
    return {"ok": False, "error": last_err, **({"operation_status": last_status} if spec.mutating else {})}


def load_tools() -> None:
    """导入各工具模块以触发注册。"""
    from app.tools import apikey, billing_tools, logs, ticket_tools  # noqa: F401
