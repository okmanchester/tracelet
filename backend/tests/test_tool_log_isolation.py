"""日志故障只能影响日志，不得重放业务或隐藏业务结果。"""

from unittest.mock import AsyncMock

import pytest

from app.tools import registry


@pytest.fixture
def tool(monkeypatch):
    execute = AsyncMock(return_value={"ticket_id": "tk_synthetic"})
    spec = registry.ToolSpec("synthetic_write", "test", {}, execute)
    monkeypatch.setitem(registry.REGISTRY, spec.name, spec)
    return spec, registry.ToolContext("a", user_id="u_a", role="customer_dev")


async def test_successful_write_is_not_replayed_when_log_fails(tool, monkeypatch, caplog):
    spec, ctx = tool
    log = AsyncMock(side_effect=ConnectionError("secret_key=synthetic-secret"))
    monkeypatch.setattr(registry, "_log_call", log)
    result = await registry.execute(spec.name, {"summary": "safe"}, ctx)
    assert result == {"ok": True, "data": {"ticket_id": "tk_synthetic"}}
    assert spec.func.await_count == log.await_count == 1
    assert "工具调用日志保存失败" in caplog.text
    assert "synthetic-secret" not in caplog.text


async def test_failed_tool_still_returns_failure_when_log_fails(tool, monkeypatch):
    spec, ctx = tool
    spec.func.side_effect = RuntimeError("secret_key=synthetic-secret")
    log = AsyncMock(side_effect=ConnectionError("log unavailable"))
    monkeypatch.setattr(registry, "_log_call", log)
    result = await registry.execute(spec.name, {}, ctx)
    assert result == {"ok": False, "error": "工具执行失败"}
    assert spec.func.await_count == spec.retries + 1
    assert log.await_count == 1


async def test_actual_execution_failure_still_uses_existing_retry_budget(tool, monkeypatch):
    spec, ctx = tool
    spec.func.side_effect = [RuntimeError("temporary"), {"ticket_id": "tk_synthetic"}]
    log = AsyncMock()
    monkeypatch.setattr(registry, "_log_call", log)
    result = await registry.execute(spec.name, {}, ctx)
    assert result["ok"] is True
    assert spec.func.await_count == 2
    log.assert_awaited_once()
    assert log.call_args.args[4] == "ok"


@pytest.mark.parametrize("idempotent, calls", [(False, 1), (True, 2)])
async def test_write_retry_requires_declared_idempotency(tool, monkeypatch, idempotent, calls):
    spec, ctx = tool
    spec.mutating, spec.idempotent = True, idempotent
    ctx.operation_id = "server_operation"
    spec.func.side_effect = ConnectionError("result unknown")
    monkeypatch.setattr(registry, "_log_call", AsyncMock())
    result = await registry.execute(spec.name, {}, ctx)
    assert spec.func.await_count == calls
    assert result["operation_status"] == "unknown"
    assert all(args.args[1].operation_id == "server_operation" for args in spec.func.await_args_list)
