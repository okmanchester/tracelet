"""保护准入入口的隔离边界、失败报告及必需清理。"""

import json

import pytest

from scripts import pilot_fixture, verify_pilot


def test_compose_inheritance_cannot_select_business_project():
    source = {"COMPOSE_FILE": "business.yml", "COMPOSE_PROJECT_NAME": "business",
              "COMPOSE_PROFILES": "danger", "COMPOSE_ENV_FILES": ".env", "OTHER": "keep"}
    clean = verify_pilot.clean_compose_env(source)
    assert clean == {"OTHER": "keep", "COMPOSE_DISABLE_ENV_FILE": "1"}
    assert source["COMPOSE_PROJECT_NAME"] == "business"


@pytest.mark.parametrize("overrides", [
    {}, {"TRACELET_PILOT_PROJECT": "business"},
    {"TRACELET_PILOT_PROJECT": "tracelet-pilot-" + "a" * 32, "MYSQL_DB": "business"},
    {"TRACELET_PILOT_PROJECT": "tracelet-pilot-" + "a" * 32,
     "MYSQL_DB": "tracelet_pilot_" + "a" * 32, "MILVUS_COLLECTION": "knowledge_chunk"},
])
def test_fixture_refuses_nonisolated_targets_before_importing_database(monkeypatch, overrides):
    for key in ("TRACELET_PILOT_PROJECT", "MYSQL_DB", "MILVUS_COLLECTION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match="拒绝操作非独立"):
        pilot_fixture.seed()


def test_fixture_accepts_matching_random_names(monkeypatch):
    monkeypatch.setenv("TRACELET_PILOT_PROJECT", "tracelet-pilot-" + "a" * 32)
    monkeypatch.setenv("MYSQL_DB", "tracelet_pilot_" + "a" * 32)
    monkeypatch.setenv("MILVUS_COLLECTION", "pilot_" + "a" * 32)
    pilot_fixture.require_isolated()


def test_startup_failure_still_reports_and_cleans_own_project(monkeypatch, tmp_path):
    pilot = verify_pilot.Pilot()
    pilot.report = tmp_path / "report"
    pilot.model_base = "https://example.invalid"
    pilot.settings.deepseek_api_key = "synthetic"
    pilot.settings.llm_chat_provider = "deepseek"
    calls = []
    child = object()
    stopped = []

    def compose(*args, **_kwargs):
        calls.append(args)
        if args[0] == "up":
            pilot.children.append(child)
            raise RuntimeError("controlled startup failure")

    monkeypatch.setattr(pilot, "compose_run", compose)
    monkeypatch.setattr(pilot, "stop", stopped.append)
    assert pilot.run() == 1
    assert stopped == [child]
    assert calls[-1] == ("down", "--volumes", "--remove-orphans")
    summary = json.loads((pilot.report / "summary.json").read_text(encoding="utf-8"))
    assert not summary["passed"]
    assert summary["cleanup"]
    assert "controlled startup failure" in summary["failure"]
    assert pilot.project.startswith("tracelet-pilot-")
    assert "--project-name" in pilot.compose
    assert pilot.compose[pilot.compose.index("--project-name") + 1] == pilot.project


def test_cleanup_failure_cannot_be_reported_as_pass(monkeypatch, tmp_path):
    pilot = verify_pilot.Pilot()
    pilot.report = tmp_path / "report"
    pilot.model_base = "https://example.invalid"
    pilot.settings.deepseek_api_key = "synthetic"
    pilot.settings.llm_chat_provider = "deepseek"

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("controlled Docker failure")

    monkeypatch.setattr(pilot, "compose_run", unavailable)
    assert pilot.run() == 1
    summary = json.loads((pilot.report / "summary.json").read_text(encoding="utf-8"))
    assert not summary["cleanup"]
    assert not summary["passed"]


def test_snapshot_waits_for_manifest_rows_after_transient_empty_read(monkeypatch):
    sleeps = []
    monkeypatch.setattr(pilot_fixture.time, "sleep", sleeps.append)

    class RecoveringCollection:
        def __init__(self):
            self.reads = 0

        def query(self, _collection, *, output_fields, **_kwargs):
            if output_fields == ["count(*)"]:
                return [{"count(*)": 1 if self.reads > 1 else 0}]
            self.reads += 1
            return [{"content": "saved"}] if self.reads > 1 else []

    assert pilot_fixture.snapshot_rows(RecoveringCollection(), "isolated", ["content"], 1) == [{"content": "saved"}]
    assert sleeps == [0.5]


def test_snapshot_never_accepts_empty_collection_or_manifest():
    class EmptyCollection:
        def query(self, _collection, *, output_fields, **_kwargs):
            return [{"count(*)": 0}] if output_fields == ["count(*)"] else []

    with pytest.raises(RuntimeError, match="快照不完整"):
        pilot_fixture.snapshot_rows(EmptyCollection(), "isolated", ["content"], 1, timeout=0)
    with pytest.raises(RuntimeError, match="清单为空"):
        pilot_fixture.snapshot_rows(EmptyCollection(), "isolated", ["content"], 0)


def test_cleanup_fences_late_helper_or_backend_start(monkeypatch, tmp_path):
    pilot = verify_pilot.Pilot()
    pilot.cleaning = True
    monkeypatch.setattr(verify_pilot.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("不能启动进程"))
    with pytest.raises(RuntimeError, match="拒绝启动新进程"):
        pilot.child(["late-helper"], tmp_path)
