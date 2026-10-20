"""显式覆盖演示数据；在 backend 下运行 python -m scripts.setup_demo --yes。"""

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="覆盖种子涉及的业务表及现有知识向量集合")
    parser.add_argument("--yes", action="store_true", help="确认覆盖已有数据")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("演示装载会覆盖业务数据和知识索引，必须指定 --yes")

    for module in ("scripts.init_db", "scripts.seed_data", "scripts.setup_demo_knowledge"):
        subprocess.run(
            [sys.executable, "-m", module] + (["--yes"] if module == "scripts.setup_demo_knowledge" else []),
            cwd=Path(__file__).resolve().parents[1],
            check=True,
        )


if __name__ == "__main__":
    main()
