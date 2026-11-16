"""命令行入口：apitest run cases/smoke.yaml --env env/dev.yaml"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from .context import load_env_file
from .report import write_html, write_junit
from .runner import Runner


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("cases", [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apitest", description="接口自动化测试框架")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="执行用例文件")
    run.add_argument("case_file", help="用例 YAML 路径")
    run.add_argument("--env", help="环境变量文件，例如 env/dev.yaml")
    run.add_argument("--tag", action="append", help="只跑带指定 tag 的用例，可重复")
    run.add_argument("--workers", type=int, default=1, help="并行度，默认 1（串行）")
    run.add_argument("--retries", type=int, default=0, help="步骤失败重试次数（仅 5xx）")
    run.add_argument("--timeout", type=int, default=10, help="单请求超时秒数")
    run.add_argument("--fail-fast", action="store_true", help="首个用例失败即停止")
    run.add_argument("--out", default="reports", help="报告输出目录")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 1

    env = load_env_file(args.env) if args.env else {}
    cases = load_cases(args.case_file)

    if args.tag:
        wanted = set(args.tag)
        cases = [c for c in cases if wanted & set(c.get("tags", []))]
    if not cases:
        print("没有匹配的用例")
        return 1

    runner = Runner(env=env, timeout=args.timeout, retries=args.retries)
    started = time.perf_counter()

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(runner.run_case, cases))
    else:
        results = []
        for case in cases:
            result = runner.run_case(case)
            results.append(result)
            print(f"[{'通过' if result.passed else '失败'}] {result.name}  {result.duration_ms}ms")
            if not result.passed and args.fail_fast:
                break

    elapsed = time.perf_counter() - started
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    out_dir = Path(args.out)
    html_path = write_html(results, str(out_dir / "report.html"))
    junit_path = write_junit(results, str(out_dir / "junit.xml"))

    print()
    print(f"用例 {len(results)}　通过 {passed}　失败 {failed}　耗时 {elapsed:.2f}s")
    print(f"报告 {html_path}")
    print(f"JUnit {junit_path}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
