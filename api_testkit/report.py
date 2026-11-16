"""报告生成：自渲染 HTML，另出 JUnit XML 便于接 CI。"""

import html
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from .runner import CaseResult

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>接口自动化测试报告</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 28px 32px; background: #f6f7f9; color: #1f2329;
         font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #86909c; font-size: 13px; margin-bottom: 22px; }}
  .cards {{ display: flex; gap: 14px; margin-bottom: 22px; }}
  .card {{ background: #fff; border-radius: 10px; padding: 16px 22px; min-width: 130px;
           border: 1px solid #e5e6eb; }}
  .card .n {{ font-size: 26px; font-weight: 700; }}
  .card .l {{ font-size: 12px; color: #86909c; margin-top: 2px; }}
  .ok {{ color: #1f9d55; }} .bad {{ color: #d93026; }} .skip {{ color: #b8860b; }}
  .case {{ background: #fff; border: 1px solid #e5e6eb; border-radius: 10px; margin-bottom: 14px; }}
  .case-head {{ display: flex; justify-content: space-between; align-items: center;
                padding: 13px 18px; border-bottom: 1px solid #f0f1f3; font-weight: 600; }}
  .tag {{ font-size: 11px; color: #4e5969; background: #f2f3f5; border-radius: 4px;
          padding: 2px 7px; margin-left: 6px; }}
  .step {{ padding: 11px 18px; border-bottom: 1px solid #f7f8fa; }}
  .step:last-child {{ border-bottom: none; }}
  .step-head {{ display: flex; justify-content: space-between; font-size: 13px; }}
  .mono {{ font-family: Consolas, 'Courier New', monospace; font-size: 12px;
           background: #f7f8fa; padding: 9px 12px; border-radius: 6px; margin-top: 8px;
           white-space: pre-wrap; word-break: break-all; color: #4e5969; }}
  .fail {{ background: #fdf3f2; color: #a8071a; padding: 8px 12px; border-radius: 6px;
           margin-top: 8px; font-size: 12px; }}
  details summary {{ cursor: pointer; color: #3370ff; font-size: 12px; margin-top: 8px; }}
</style>
</head>
<body>
<h1>接口自动化测试报告</h1>
<div class="sub">执行时间 {started}　耗时 {duration}</div>
<div class="cards">
  <div class="card"><div class="n">{total}</div><div class="l">用例总数</div></div>
  <div class="card"><div class="n ok">{passed}</div><div class="l">通过</div></div>
  <div class="card"><div class="n bad">{failed}</div><div class="l">失败</div></div>
  <div class="card"><div class="n">{steps}</div><div class="l">步骤数</div></div>
</div>
{cases}
</body>
</html>
"""

CASE_TEMPLATE = """<div class="case">
  <div class="case-head">
    <div>{name}{tags}</div>
    <div class="{cls}">{label}　{dur}ms</div>
  </div>
  {steps}
</div>
"""

STEP_TEMPLATE = """<div class="step">
  <div class="step-head">
    <span>{name}</span>
    <span class="{cls}">{status}　{dur}ms</span>
  </div>
  <div class="mono">{method} {url}</div>
  {failures}
  <details><summary>请求 / 响应快照</summary>
    <div class="mono">{req}</div>
    <div class="mono">{resp}</div>
  </details>
</div>
"""


def write_html(results: list[CaseResult], out_path: str) -> str:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    total_ms = sum(r.duration_ms for r in results)
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    step_count = sum(len(r.steps) for r in results)

    cases_html = []
    for r in results:
        steps_html = []
        for s in r.steps:
            cls = "skip" if s.skipped else ("ok" if s.passed else "bad")
            status = "跳过" if s.skipped else ("通过" if s.passed else "失败")
            failures = ""
            if s.failures:
                failures = '<div class="fail">' + "<br>".join(html.escape(f) for f in s.failures) + "</div>"
            steps_html.append(STEP_TEMPLATE.format(
                name=html.escape(s.name), cls=cls, status=status, dur=s.duration_ms,
                method=s.method, url=html.escape(s.url), failures=failures,
                req=html.escape(s.request_snapshot), resp=html.escape(s.response_snapshot)))
        tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in r.tags)
        cases_html.append(CASE_TEMPLATE.format(
            name=html.escape(r.name), tags=tags,
            cls="ok" if r.passed else "bad",
            label="通过" if r.passed else "失败",
            dur=r.duration_ms, steps="".join(steps_html)))

    content = TEMPLATE.format(
        started=started, duration=f"{total_ms / 1000:.2f}s",
        total=len(results), passed=passed, failed=failed, steps=step_count,
        cases="".join(cases_html))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(content, encoding="utf-8")
    return out_path


def write_junit(results: list[CaseResult], out_path: str) -> str:
    suite = ET.Element("testsuite", {
        "name": "api-testkit",
        "tests": str(len(results)),
        "failures": str(sum(1 for r in results if not r.passed)),
        "time": f"{sum(r.duration_ms for r in results) / 1000:.3f}",
    })
    for r in results:
        case = ET.SubElement(suite, "testcase", {
            "name": r.name, "time": f"{r.duration_ms / 1000:.3f}"})
        if not r.passed:
            msg = "; ".join(f for s in r.steps for f in s.failures) or "用例失败"
            ET.SubElement(case, "failure", {"message": msg[:500]})
    tree = ET.ElementTree(suite)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path
