"""断言引擎：每个断言返回 (是否通过, 说明)。"""

import json
import re
from typing import Any

from jsonschema import Draft7Validator


def _jsonpath(data: Any, path: str) -> Any:
    """支持 a.b[0].c 这种极简路径，不引入 jsonpath 依赖。"""
    node = data
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("["):
            try:
                node = node[int(token[1:-1])]
            except (IndexError, TypeError):
                return None
        elif isinstance(node, list) and token.isdigit():
            try:
                node = node[int(token)]
            except IndexError:
                return None
        elif isinstance(node, dict) and token in node:
            node = node[token]
        else:
            return None
    return node


def check(kind: str, actual_response, spec: dict) -> tuple[bool, str]:
    body = actual_response.text
    try:
        payload = actual_response.json()
    except (json.JSONDecodeError, ValueError):
        payload = None

    if kind == "status":
        ok = actual_response.status_code == spec["expect"]
        return ok, f"状态码期望 {spec['expect']}，实际 {actual_response.status_code}"

    if kind == "jsonpath":
        value = _jsonpath(payload, spec["path"])
        ok = value == spec["equals"]
        return ok, f"{spec['path']} 期望 {spec['equals']!r}，实际 {value!r}"

    if kind == "gte":
        value = _jsonpath(payload, spec["path"])
        ok = isinstance(value, (int, float)) and value >= spec["value"]
        return ok, f"{spec['path']} 期望 >= {spec['value']}，实际 {value!r}"

    if kind == "exists":
        value = _jsonpath(payload, spec["path"])
        ok = value is not None
        return ok, f"{spec['path']} {'存在' if ok else '不存在'}"

    if kind == "contains":
        ok = spec["text"] in body
        return ok, f"响应{'包含' if ok else '不包含'} {spec['text']!r}"

    if kind == "regex":
        ok = re.search(spec["pattern"], body) is not None
        return ok, f"正则 {spec['pattern']!r} {'命中' if ok else '未命中'}"

    if kind == "schema":
        errors = sorted(Draft7Validator(spec["schema"]).iter_errors(payload), key=lambda e: e.path)
        if errors:
            first = errors[0]
            return False, f"Schema 校验失败：{'/'.join(map(str, first.path))} {first.message}"
        return True, "Schema 校验通过"

    if kind == "response_time":
        ok = actual_response.elapsed.total_seconds() * 1000 <= spec["max_ms"]
        return ok, f"耗时 {actual_response.elapsed.total_seconds() * 1000:.0f}ms，上限 {spec['max_ms']}ms"

    return False, f"未知断言类型：{kind}"


SUPPORTED = ("status", "jsonpath", "gte", "exists", "contains", "regex", "schema", "response_time")

# 每种断言的必需字段：check() 直接下标取，少一个就 KeyError，故加载阶段按此表校验
REQUIRED = {
    "status": ("expect",),
    "jsonpath": ("path", "equals"),
    "gte": ("path", "value"),
    "exists": ("path",),
    "contains": ("text",),
    "regex": ("pattern",),
    "schema": ("schema",),
    "response_time": ("max_ms",),
}
