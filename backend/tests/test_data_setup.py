"""数据准备入口回归测试；存储及演示子进程均使用替身。"""

import builtins
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from scripts import init_db, setup_demo


@pytest.fixture
def storage(monkeypatch):
    from app import schema_migrations
    from app.rag import store

    migration = Mock(return_value="schema ready")
    vector = Mock()
    vector.has_collection.return_value = True
    monkeypatch.setattr(schema_migrations, "migrate", migration)
    monkeypatch.setattr(store, "get_client", lambda: vector)
    return migration, vector


@pytest.mark.parametrize("args", [[], ["--yes"]])
def test_repeated_safe_init_preserves_existing_storage(storage, args):
    migration, vector = storage
    init_db.main(args)
    init_db.main(args)
    from app.db import sync_engine

    assert migration.call_args_list == [
        call(sync_engine, "upgrade", confirmed=bool(args)),
        call(sync_engine, "upgrade", confirmed=bool(args)),
    ]
    vector.drop_collection.assert_not_called()
    vector.create_collection.assert_not_called()


def test_safe_init_creates_missing_collection_only_once(storage):
    migration, vector = storage
    exists = False

    def create_collection(**kwargs):
        nonlocal exists
        exists = True

    vector.has_collection.side_effect = lambda name: exists
    vector.create_collection.side_effect = create_collection
    init_db.main([])
    init_db.main([])
    assert all(item.args[1] == "upgrade" for item in migration.call_args_list)
    vector.drop_collection.assert_not_called()
    vector.create_collection.assert_called_once()


def test_unconfirmed_reset_rejected_before_loading_storage(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "app" or name.startswith("app."):
            pytest.fail("拒绝参数时不应加载存储依赖")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(SystemExit) as exc:
        init_db.main(["--recreate"])
    assert exc.value.code == 2


def test_confirmed_reset_drops_before_recreating(storage):
    migration, vector = storage
    events = []
    migration.side_effect = lambda engine, action, **kwargs: events.append(action)
    vector.drop_collection.side_effect = lambda name: events.append("drop_collection")
    vector.has_collection.side_effect = [True, False]
    vector.create_collection.side_effect = lambda **kwargs: events.append("create_collection")
    init_db.main(["--recreate", "--yes"])
    assert events == ["reset", "drop_collection", "create_collection"]
    assert migration.call_args.kwargs == {"confirmed": True}


def test_mysql_init_failure_stops_before_vector_operations(storage):
    migration, vector = storage
    migration.side_effect = RuntimeError("test database unavailable")
    with pytest.raises(RuntimeError, match="test database unavailable"):
        init_db.main([])
    vector.has_collection.assert_not_called()


def test_unadopted_database_failure_is_reported_without_touching_vectors(storage, capsys):
    from app.schema_migrations import MigrationError

    migration, vector = storage
    migration.side_effect = MigrationError("非空数据库尚未接管")
    with pytest.raises(SystemExit) as error:
        init_db.main([])
    assert error.value.code == 1
    assert "非空数据库尚未接管" in capsys.readouterr().err
    vector.has_collection.assert_not_called()


def test_unconfirmed_demo_does_not_start_any_step(monkeypatch):
    run = Mock()
    monkeypatch.setattr(setup_demo.subprocess, "run", run)
    with pytest.raises(SystemExit) as exc:
        setup_demo.main([])
    assert exc.value.code == 2
    run.assert_not_called()


def test_confirmed_demo_runs_steps_in_order(monkeypatch):
    run = Mock()
    monkeypatch.setattr(setup_demo.subprocess, "run", run)
    setup_demo.main(["--yes"])
    assert [call.args[0] for call in run.call_args_list] == [
        [sys.executable, "-m", module]
        for module in ("scripts.init_db", "scripts.seed_data", "scripts.ingest_knowledge")
    ]
    for invocation in run.call_args_list:
        assert invocation.kwargs == {"cwd": Path(setup_demo.__file__).resolve().parents[1], "check": True}


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_demo_failure_stops_remaining_steps(monkeypatch, failed_step):
    error = subprocess.CalledProcessError(7, ["test-step"])
    run = Mock(side_effect=[None] * failed_step + [error])
    monkeypatch.setattr(setup_demo.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        setup_demo.main(["--yes"])
    assert exc.value.returncode == 7
    assert run.call_count == failed_step + 1
