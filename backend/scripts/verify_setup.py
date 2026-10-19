"""隔离验收：python -m scripts.verify_setup；需要可用的 Docker Compose。"""

import argparse
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
CONTAINER_REPORT = "/tmp/tracelet-verify.xml"


def validate_report(path: Path) -> int:
    """只接受完整且零失败、零错误、零跳过的 pytest JUnit 报告。"""
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root)
    else:
        raise ValueError("无法识别 JUnit 报告根节点")
    if not suites or any(suite.tag != "testsuite" for suite in suites):
        raise ValueError("JUnit 报告缺少有效测试套件")

    totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    for suite in suites:
        actual = {
            "tests": len(suite.findall("testcase")),
            "failures": len(suite.findall("testcase/failure")),
            "errors": len(suite.findall("testcase/error")),
            "skipped": len(suite.findall("testcase/skipped")),
        }
        declared = {key: int(suite.get(key, "")) for key in totals}
        if declared != actual:
            raise ValueError("JUnit 报告统计与测试记录不一致")
        for key in totals:
            totals[key] += actual[key]
    if totals["tests"] == 0 or any(totals[key] for key in ("failures", "errors", "skipped")):
        raise ValueError(f"验收报告未通过：{totals}")
    return totals["tests"]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    project = f"tracelet-verify-{uuid4().hex}"
    report_dir = ROOT / ".pytest_cache" / "verify" / project
    report = report_dir / "junit.xml"
    compose = [
        "docker", "compose", "--file", str(ROOT / "compose.verify.yml"),
        "--project-name", project, "--project-directory", str(ROOT),
    ]
    env = os.environ.copy()
    for name in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
        env.pop(name, None)
    env["COMPOSE_DISABLE_ENV_FILE"] = "1"

    def run(*args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*compose, *args], cwd=ROOT, env=env, check=False, timeout=timeout,
        )

    result = 1
    count = 0
    print(f"[verify-setup] 项目：{project}", flush=True)
    print(f"[verify-setup] 报告：{report}", flush=True)
    try:
        report_dir.mkdir(parents=True, exist_ok=False)
        execution = run("up", "--build", "--abort-on-container-exit", "--exit-code-from", "tests")
        result = execution.returncode if execution.returncode > 0 else 1
        copied = run("cp", f"tests:{CONTAINER_REPORT}", str(report), timeout=60)
        if copied.returncode != 0:
            raise ValueError("无法取得本次验收报告，验收未通过")
        count = validate_report(report)
        if execution.returncode != 0:
            raise ValueError(f"容器执行失败，退出码：{execution.returncode}")
        result = 0
    except KeyboardInterrupt:
        result = 130
        print("[verify-setup] 验收中断，正在清理本次资源。", file=sys.stderr)
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as exc:
        print(f"[verify-setup] {exc}", file=sys.stderr)
    finally:
        cleanup = ["down", "--volumes", "--remove-orphans"]
        try:
            cleaned = run(*cleanup, timeout=120)
            if cleaned.returncode != 0:
                raise RuntimeError(f"清理退出码：{cleaned.returncode}")
        except (OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            result = result or 1
            print(f"[verify-setup] 清理失败：{exc}", file=sys.stderr)
            print(f"[verify-setup] 手动清理：{shlex.join([*compose, *cleanup])}", file=sys.stderr)

    if result == 0:
        print(f"[verify-setup] 验收通过：{count} 项测试，零失败、零错误、零跳过；本次资源已清理。")
    return result


if __name__ == "__main__":
    sys.exit(main())
