"""使用真实 GNU Make 和临时脚本验证命令入口，不连接任何存储服务。"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def make_workspace(request):
    make = shutil.which("make") or shutil.which("mingw32-make")
    if make is None:
        pytest.skip("需要 GNU Make 验证 Makefile 入口")
    tmp_path = request.getfixturevalue("tmp_path")
    scripts = tmp_path / "backend" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    for name in ("init_db", "setup_demo", "seed_data", "ingest_knowledge", "verify_setup"):
        (scripts / f"{name}.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "with open(os.environ['TRACELET_TEST_EVENTS'], 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps([Path(__file__).stem, sys.argv[1:]]) + '\\n')\n"
            "sys.exit(int(os.environ.get('TRACELET_TEST_EXIT_CODE', '0')))\n",
            encoding="utf-8",
        )
    events = tmp_path / "events.jsonl"
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["TRACELET_TEST_EVENTS"] = str(events)
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "CONFIRM_RESET"):
        env.pop(name, None)
    makefile = Path(__file__).resolve().parents[2] / "Makefile"

    def run(target, confirmation=None, exit_code=0):
        command = [make, "-f", str(makefile), "-j4", target]
        if confirmation is not None:
            command.append(f"CONFIRM_RESET={confirmation}")
        env["TRACELET_TEST_EXIT_CODE"] = str(exit_code)
        result = subprocess.run(
            command, cwd=tmp_path, env=env, capture_output=True, timeout=30, check=False,
        )
        calls = (
            [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
            if events.exists() else []
        )
        return result, calls

    return run


@pytest.mark.parametrize("target", ["init-db", "setup"])
def test_default_make_entry_only_initializes_safely(make_workspace, target):
    result, calls = make_workspace(target)
    assert result.returncode == 0, result.stderr
    assert calls == [["init_db", []]]


@pytest.mark.parametrize("target", ["reset-db", "setup-demo"])
@pytest.mark.parametrize("confirmation", [None, "0", "1 2"])
def test_make_rejects_unconfirmed_operations_before_any_step(
    make_workspace, target, confirmation,
):
    result, calls = make_workspace(target, confirmation)
    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize(
    ("target", "expected"),
    [("reset-db", ["init_db", ["--recreate", "--yes"]]),
     ("setup-demo", ["setup_demo", ["--yes"]])],
)
def test_make_passes_confirmation_to_script(make_workspace, target, expected):
    result, calls = make_workspace(target, "1")
    assert result.returncode == 0, result.stderr
    assert calls == [expected]


def test_make_propagates_demo_failure(make_workspace):
    result, calls = make_workspace("setup-demo", "1", exit_code=7)
    assert result.returncode != 0
    assert calls == [["setup_demo", ["--yes"]]]


def test_make_verify_uses_isolated_runner(make_workspace):
    result, calls = make_workspace("verify-setup")
    assert result.returncode == 0, result.stderr
    assert calls == [["verify_setup", []]]
