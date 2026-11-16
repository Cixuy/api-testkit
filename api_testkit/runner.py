"""用例执行引擎：把 YAML 用例跑成结果对象。"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

from .assertions import check
from .context import Context


@dataclass
class StepResult:
    name: str
    method: str = ""
    url: str = ""
    status_code: int = 0
    duration_ms: int = 0
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    request_snapshot: str = ""
    response_snapshot: str = ""
    skipped: bool = False


@dataclass
class CaseResult:
    name: str
    tags: list[str] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps)

    @property
    def duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.steps)


class Runner:
    def __init__(self, env: dict | None = None, timeout: int = 10, retries: int = 0, verbose: bool = False):
        self.base_env = env or {}
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose

    def run_case(self, case: dict) -> CaseResult:
        # 每个用例独立上下文，避免并行执行时变量串味
        ctx = Context(self.base_env)
        ctx.set("run_id", uuid.uuid4().hex[:8])

        result = CaseResult(name=case.get("name", "未命名用例"), tags=case.get("tags", []))
        steps = case.get("steps", [])
        failed_names: set[str] = set()

        for step in steps:
            sr = self._run_step(step, ctx, failed_names)
            result.steps.append(sr)
            if not sr.passed:
                failed_names.add(step.get("name", ""))
                if case.get("stop_on_failure", False):
                    break
        return result

    def _run_step(self, step: dict, ctx: Context, failed_names: set) -> StepResult:
        name = step.get("name", "未命名步骤")
        sr = StepResult(name=name)

        # 依赖步骤失败时跳过，并标记为跳过而不是失败
        depends = step.get("depends_on")
        if depends and depends in failed_names:
            sr.skipped = True
            sr.failures.append(f"依赖步骤 {depends} 未通过，本步骤跳过")
            return sr

        req = step.get("request", {})
        method = req.get("method", "GET").upper()
        url = ctx.render(req.get("url", ""))
        headers = ctx.render(req.get("headers", {}))
        params = ctx.render(req.get("params", {}))
        body = ctx.render(req.get("json"))

        sr.method, sr.url = method, url
        sr.request_snapshot = self._dump_request(method, url, headers, params, body)

        attempt = 0
        resp = None
        while attempt <= self.retries:
            start = time.perf_counter()
            try:
                resp = requests.request(method, url, headers=headers, params=params,
                                        json=body, timeout=self.timeout)
            except requests.RequestException as e:
                sr.duration_ms = int((time.perf_counter() - start) * 1000)
                sr.passed = False
                sr.failures.append(f"请求异常：{e}")
                resp = None
                break
            sr.duration_ms = int((time.perf_counter() - start) * 1000)
            # 只在 5xx 时重试，4xx 是代码或数据问题，重试无意义
            if resp.status_code < 500 or attempt == self.retries:
                break
            attempt += 1

        if resp is None:
            return sr

        sr.status_code = resp.status_code
        sr.response_snapshot = self._dump_response(resp)

        for raw_spec in step.get("assert", []):
            spec = ctx.render(raw_spec)
            for kind, payload in spec.items():
                ok, note = check(kind, resp, payload)
                if not ok:
                    sr.passed = False
                    sr.failures.append(note)

        # 变量提取：失败也执行，方便后续步骤继续传参
        for key, path in (step.get("extract") or {}).items():
            from .assertions import _jsonpath
            try:
                data = resp.json()
            except ValueError:
                data = None
            ctx.set(key, _jsonpath(data, path))

        return sr

    @staticmethod
    def _dump_request(method, url, headers, params, body) -> str:
        lines = [f"{method} {url}"]
        if params:
            lines.append(f"参数: {params}")
        if headers:
            lines.append(f"请求头: {headers}")
        if body is not None:
            lines.append("请求体: " + json_dumps(body))
        return "\n".join(lines)

    @staticmethod
    def _dump_response(resp) -> str:
        text = resp.text or ""
        if len(text) > 1500:
            text = text[:1500] + "...（已截断）"
        return f"状态码: {resp.status_code}\n响应体: {text}"


def json_dumps(value: Any) -> str:
    import json
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
