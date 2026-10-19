"""验收编排的失败判定和资源边界测试；不调用真实 Docker。"""

import subprocess
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from scripts import verify_setup


def junit(*, outcome=None):
    suite = ET.Element("testsuite", tests="1", failures="0", errors="0", skipped="0")
    case = ET.SubElement(suite, "testcase", name="synthetic_test")
    if outcome:
        ET.SubElement(case, outcome)
        suite.set({"failure": "failures", "error": "errors", "skipped": "skipped"}[outcome], "1")
    return ET.tostring(suite, encoding="unicode")


@pytest.fixture
def docker(monkeypatch, tmp_path):
    monkeypatch.setattr(verify_setup, "ROOT", tmp_path)
    state = SimpleNamespace(
        calls=[], up_code=0, copy_code=0, cleanup_code=0,
        up_error=None, cleanup_error=None, report=junit(),
    )

    def run(command, **kwargs):
        operation = command[8]
        state.calls.append((operation, command, kwargs))
        if operation == "up":
            if state.up_error is not None:
                raise state.up_error
            code = state.up_code
        elif operation == "cp":
            if state.copy_code == 0 and state.report is not None:
                verify_setup.Path(command[-1]).write_text(state.report, encoding="utf-8")
            code = state.copy_code
        elif operation == "down":
            if state.cleanup_error is not None:
                raise state.cleanup_error
            code = state.cleanup_code
        else:
            pytest.fail(f"意外的 Docker 操作：{command}")
        return subprocess.CompletedProcess(command, code)

    monkeypatch.setattr(verify_setup.subprocess, "run", run)
    return state


def test_success_requires_report_and_cleanup(docker, capsys):
    assert verify_setup.main([]) == 0
    assert [op for op, _, _ in docker.calls] == ["up", "cp", "down"]
    assert "验收通过" in capsys.readouterr().out


def test_commands_are_scoped_to_one_unique_project(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "business.yml")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "business")
    monkeypatch.setenv("COMPOSE_ENV_FILES", "business.env")
    monkeypatch.setenv("COMPOSE_PROFILES", "business")
    assert verify_setup.main([]) == 0
    prefixes = [command[:8] for _, command, _ in docker.calls]
    assert all(prefix == prefixes[0] for prefix in prefixes)
    prefix = prefixes[0]
    assert prefix[:4] == ["docker", "compose", "--file", str(tmp_path / "compose.verify.yml")]
    assert prefix[4] == "--project-name"
    assert prefix[5].startswith("tracelet-verify-")
    assert prefix[6:] == ["--project-directory", str(tmp_path)]
    assert docker.calls[0][1][8:] == [
        "up", "--build", "--abort-on-container-exit", "--exit-code-from", "tests",
    ]
    assert docker.calls[-1][1][8:] == ["down", "--volumes", "--remove-orphans"]
    for _, _, kwargs in docker.calls:
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is False
        assert not kwargs.get("shell", False)
        assert kwargs["env"]["COMPOSE_DISABLE_ENV_FILE"] == "1"
        for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES", "COMPOSE_PROFILES"):
            assert name not in kwargs["env"]
    report = verify_setup.Path(docker.calls[1][1][-1])
    assert report == tmp_path / ".pytest_cache" / "verify" / prefix[5] / "junit.xml"


def test_repeated_runs_do_not_share_projects_or_reports(docker):
    assert verify_setup.main([]) == 0
    assert verify_setup.main([]) == 0
    assert docker.calls[0][1][5] != docker.calls[3][1][5]
    assert docker.calls[1][1][-1] != docker.calls[4][1][-1]


def test_nonzero_container_exit_cannot_pass_even_with_passing_report(docker, capsys):
    docker.up_code = 7
    assert verify_setup.main([]) == 7
    assert docker.calls[-1][0] == "down"
    assert "验收通过" not in capsys.readouterr().out


def test_dependency_startup_failure_is_nonzero_and_cleaned(docker):
    docker.up_code = 42
    docker.copy_code = 1
    assert verify_setup.main([]) == 42
    assert docker.calls[-1][0] == "down"


@pytest.mark.parametrize("report", [None, "broken xml", junit(outcome="skipped"), junit(outcome="failure")])
def test_missing_invalid_or_unsuccessful_report_cannot_pass(docker, report, capsys):
    docker.report = report
    assert verify_setup.main([]) != 0
    assert docker.calls[-1][0] == "down"
    assert "验收通过" not in capsys.readouterr().out


def test_report_copy_failure_cannot_pass(docker):
    docker.copy_code = 1
    assert verify_setup.main([]) != 0
    assert docker.calls[-1][0] == "down"


@pytest.mark.parametrize("error", [OSError("synthetic Docker error"), subprocess.TimeoutExpired("up", 1)])
def test_process_errors_still_trigger_cleanup(docker, error):
    docker.up_error = error
    assert verify_setup.main([]) != 0
    assert [op for op, _, _ in docker.calls] == ["up", "down"]


def test_interrupt_is_nonzero_and_cleaned(docker):
    docker.up_error = KeyboardInterrupt()
    assert verify_setup.main([]) == 130
    assert [op for op, _, _ in docker.calls] == ["up", "down"]


@pytest.mark.parametrize("error", [None, OSError("cleanup denied"), KeyboardInterrupt()])
def test_cleanup_failure_cannot_report_success(docker, error, capsys):
    docker.cleanup_code = 2
    docker.cleanup_error = error
    assert verify_setup.main([]) != 0
    output = capsys.readouterr()
    assert "验收通过" not in output.out
    assert "清理失败" in output.err
    assert "手动清理" in output.err
    assert docker.calls[0][1][5] in output.err


def test_cleanup_failure_preserves_original_failure(docker):
    docker.up_code = 9
    docker.cleanup_code = 2
    assert verify_setup.main([]) == 9


@pytest.mark.parametrize("wrapped", [False, True])
def test_valid_junit_is_accepted(tmp_path, wrapped):
    xml = junit()
    if wrapped:
        xml = f"<testsuites>{xml}</testsuites>"
    path = tmp_path / "report.xml"
    path.write_text(xml, encoding="utf-8")
    assert verify_setup.validate_report(path) == 1


@pytest.mark.parametrize("xml", [
    "<unknown/>",
    "<testsuites/>",
    "<testsuites><unknown/></testsuites>",
    '<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
    '<testsuite tests="1" failures="0" errors="0" skipped="0"/>',
    '<testsuite tests="bad" failures="0" errors="0" skipped="0"><testcase/></testsuite>',
    "<testsuite><testcase/></testsuite>",
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase><skipped/></testcase></testsuite>',
    junit(outcome="failure"),
    junit(outcome="error"),
    junit(outcome="skipped"),
])
def test_empty_inconsistent_or_unsuccessful_junit_is_rejected(tmp_path, xml):
    path = tmp_path / "report.xml"
    path.write_text(xml, encoding="utf-8")
    with pytest.raises(ValueError):
        verify_setup.validate_report(path)
