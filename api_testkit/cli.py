"""命令行入口：apitest run cases/smoke.yaml --env env/dev.yaml"""

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from .assertions import REQUIRED
from .context import load_env_file
from .report import write_html, write_junit
from .runner import Runner


class CaseFileError(Exception):
    """用例文件层面的问题（不存在 / 语法错 / 结构不对），由 main 统一转成退码 2。"""


def _normalize_tags(raw, case_name: str) -> list[str]:
    """tags 允许写成 ["smoke","review"]，也允许 `tags: smoke` 或 `tags: smoke, review`。

    YAML 里手写时很容易漏掉方括号，而字符串是可迭代的：不归一化的话
    --list 会显示成 s,m,o,k,e，--tag smoke 又一条都筛不出来，看着像「用例丢了」。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        out = []
        for t in raw:
            if not isinstance(t, (str, int, float)):
                raise CaseFileError(f"用例「{case_name}」的 tags 里有非法元素：{t!r}")
            out.append(str(t).strip())
        return [t for t in out if t]
    raise CaseFileError(f"用例「{case_name}」的 tags 应为列表或字符串，当前是 {type(raw).__name__}")


def _invalid(case_name: str, sname: str, msg: str):
    raise CaseFileError(f"用例「{case_name}」步骤「{sname}」{msg}")


def _validate_asserts(case_name: str, steps) -> None:
    """断言写错在发请求前拦下：原先漏一个 `equals` 就是 KeyError traceback，整个 run 中断。"""
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        raise CaseFileError(f"用例「{case_name}」的 steps 应是步骤映射构成的列表")
    names = {s.get("name") for s in steps}
    for step in steps:
        sname = step.get("name", "(未命名步骤)")
        dep = step.get("depends_on")
        if dep is not None and dep not in names:
            _invalid(case_name, sname, f"的 depends_on 指向不存在的步骤 {dep!r}")
        raw = step.get("assert") or []
        if not isinstance(raw, list):
            _invalid(case_name, sname, f"的 assert 应为列表，每项一个断言，当前是 {type(raw).__name__}")
        for item in raw:
            if not isinstance(item, dict) or len(item) != 1:
                _invalid(case_name, sname, f"的断言应写成「类型: 参数」的单键映射，实际是 {item!r}")
            kind, payload = next(iter(item.items()))
            if kind not in REQUIRED:
                _invalid(case_name, sname, f"未知断言类型 {kind!r}，支持：{'/'.join(REQUIRED)}")
            missing = [k for k in REQUIRED[kind] if k not in payload]
            if missing:
                _invalid(case_name, sname, f"的 {kind} 缺少字段 {'/'.join(missing)}")
            if kind == "regex":
                try:
                    re.compile(payload["pattern"])
                except re.error as e:
                    _invalid(case_name, sname, f"的正则不合法：{e}")


def load_cases(path: str) -> list[dict]:
    """读用例文件。路径写错 / YAML 语法错时给一行可读的报错，不抛 traceback。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise CaseFileError(f"用例文件不存在：{path}")
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"（第 {mark.line + 1} 行第 {mark.column + 1} 列）" if mark else ""
        raise CaseFileError(f"用例文件 YAML 解析失败：{path}{where}\n  {getattr(e, 'problem', e)}")
    if not isinstance(data, dict):
        raise CaseFileError(f"用例文件顶层应为映射（含 cases 键）：{path}")
    cases = data.get("cases", [])
    if not isinstance(cases, list):
        raise CaseFileError(f"cases 应为列表：{path}")
    for c in cases:
        if not isinstance(c, dict):
            raise CaseFileError(f"cases 里每一项都应是映射，发现 {type(c).__name__}：{path}")
        name = str(c.get("name", "(未命名)"))
        c["tags"] = _normalize_tags(c.get("tags"), name)
        _validate_asserts(name, c.get("steps", []))
    return cases


def _dw(s: str) -> int:
    """按终端显示宽度算长度：中日韩字符占两列，否则对齐会歪。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dw(s))


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
    run.add_argument("--list", action="store_true", help="只列出用例（名称/tag/步骤数），不发任何请求")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        return 1

    env = load_env_file(args.env) if args.env else {}
    try:
        cases = load_cases(args.case_file)
    except CaseFileError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2

    if args.tag:
        wanted = set(args.tag)
        cases = [c for c in cases if wanted & set(c.get("tags", []))]
    if not cases:
        print("没有匹配的用例")
        return 1

    # --list：先看清要跑什么再决定跑不跑。CI 里可以用它做"用例清单"快照，
    # 也方便排查"为什么这个用例没被执行"（tag 过滤掉了？文件名写错了？）
    if args.list:
        print(f"{args.case_file}：{len(cases)} 个用例")
        for c in cases:
            tags = ",".join(c.get("tags", [])) or "-"
            steps = len(c.get("steps", []))
            print(f"  {_pad(str(c.get('name', '(未命名)')), 26)} tag={_pad(tags, 18)} 步骤 {steps}")
        return 0

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
