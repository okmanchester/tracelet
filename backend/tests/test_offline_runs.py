"""离线任务禁用时不能加载业务执行依赖，HTTP 仍提供只读指标。"""

import builtins
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.api import eval as eval_api
from app.config import settings
from app.db import get_db
from app.deps import CurrentUser, require_internal
from app.offline_runs import OfflineRunDisabled, offline_runs_allowed
from benchmark import loadtest
from eval import run_eval
from fastapi import FastAPI


@pytest.mark.parametrize("env", ["pilot", "production", "staging", "", "custom"])
def test_non_development_environments_disable_offline_runs(monkeypatch, env):
    monkeypatch.setattr(settings, "app_env", env)
    assert not offline_runs_allowed()


@pytest.mark.parametrize("env", ["dev", "test", "eval", " DEV "])
def test_explicit_development_environments_remain_available(monkeypatch, env):
    monkeypatch.setattr(settings, "app_env", env)
    assert offline_runs_allowed()


@pytest.fixture
def blocked_business_imports(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "pilot")
    original = builtins.__import__
    attempted = []

    def checked_import(name, *args, **kwargs):
        if name.startswith(("app.agents", "app.cache", "app.db", "app.models")):
            attempted.append(name)
            raise AssertionError(f"禁用时不得加载业务依赖：{name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    return attempted


@pytest.mark.parametrize("entry,args", [
    (run_eval.evaluate, ()),
    (loadtest.main, ()),
    (loadtest._run_batch, ("probe",)),
    (loadtest._stage_breakdown, (["trace_probe"],)),
])
async def test_direct_calls_reject_before_business_dependencies(blocked_business_imports, entry, args):
    with pytest.raises(OfflineRunDisabled, match="禁止运行评估和压测"):
        await entry(*args)
    assert blocked_business_imports == []


def test_eval_main_rejects_before_database_and_event_loop(blocked_business_imports):
    with pytest.raises(OfflineRunDisabled):
        run_eval.main()
    assert blocked_business_imports == []


@pytest.mark.parametrize("module", ["eval.run_eval", "benchmark.loadtest"])
def test_cli_rejects_without_external_connections(module):
    env = os.environ.copy()
    env.update({"APP_ENV": "pilot", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run(
        [sys.executable, "-B", "-m", module],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "OfflineRunDisabled" in result.stderr
    assert "禁止运行评估和压测" in result.stderr
    assert "ConnectionError" not in result.stderr


@pytest.fixture
def api_app():
    app = FastAPI()
    app.include_router(eval_api.router)
    app.dependency_overrides[require_internal] = lambda: CurrentUser(
        user_id="support_probe", username="support_probe", display_name="probe",
        role="support", tenant_id="internal",
    )
    return app


async def test_http_rejects_without_evaluating(monkeypatch, api_app):
    monkeypatch.setattr(settings, "app_env", "pilot")
    evaluate = AsyncMock(side_effect=AssertionError("不能执行评估"))
    monkeypatch.setattr(run_eval, "evaluate", evaluate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test") as c:
        response = await c.post("/api/eval/run")
    assert response.status_code == 403
    evaluate.assert_not_awaited()


async def test_development_http_retains_existing_success_contract(monkeypatch, api_app):
    monkeypatch.setattr(settings, "app_env", "dev")
    payload = {"total_cases": 1, "intent_accuracy": 1.0, "badcases": []}
    evaluate = AsyncMock(return_value=payload)
    monkeypatch.setattr(run_eval, "evaluate", evaluate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test") as c:
        response = await c.post("/api/eval/run")
    assert response.status_code == 200
    assert response.json() == payload
    evaluate.assert_awaited_once_with()


@pytest.mark.parametrize("env,allowed", [("pilot", False), ("dev", True)])
async def test_metrics_remain_read_only_with_server_capability(monkeypatch, api_app, env, allowed):
    monkeypatch.setattr(settings, "app_env", env)

    class EmptyRows:
        def all(self):
            return []

    # 刻意不提供 add/commit/Redis/模型接口；若产生写入或执行则失败。
    class ReadOnlyDB:
        scalar = AsyncMock(return_value=0)
        execute = AsyncMock(return_value=EmptyRows())

    db = ReadOnlyDB()
    api_app.dependency_overrides[get_db] = lambda: db
    summary = AsyncMock(return_value=[])
    monkeypatch.setattr(eval_api.cost, "summary_by_tenant", summary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test") as c:
        response = await c.get("/api/metrics")
    assert response.status_code == 200
    assert response.json()["capabilities"] == {"offline_runs": allowed}
    assert response.json()["conversations"]["total"] == 0
    assert response.json()["tickets"] == {"by_status": {}, "by_priority": {}}
    assert db.scalar.await_count == 3
    assert db.execute.await_count == 3
    summary.assert_awaited_once_with()
