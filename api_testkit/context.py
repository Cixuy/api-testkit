"""变量上下文：负责 ${var} 插值、环境变量合并、跨步骤取值。"""

import os
import re
from typing import Any

VAR_PATTERN = re.compile(r"\$\{([a-zA-Z_][\w.]*)\}")


class Context:
    def __init__(self, env: dict | None = None):
        self._store: dict[str, Any] = {}
        self._store.update(env or {})

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        # 支持 a.b.c 形式的取值，方便从嵌套响应里拿字段
        node: Any = self._store
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def update(self, data: dict) -> None:
        self._store.update(data)

    def render(self, value: Any) -> Any:
        """递归替换字符串里的 ${var}；整串就是一个变量时保留原始类型。"""
        if isinstance(value, str):
            whole = VAR_PATTERN.fullmatch(value)
            if whole:
                resolved = self.get(whole.group(1))
                return value if resolved is None else resolved
            return VAR_PATTERN.sub(lambda m: str(self.get(m.group(1), m.group(0))), value)
        if isinstance(value, dict):
            return {k: self.render(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render(v) for v in value]
        return value

    def snapshot(self) -> dict:
        return dict(self._store)


def load_env_file(path: str) -> dict:
    """读取 env yaml，允许用 ${ENV_NAME} 引用系统环境变量。"""
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    result = {}
    for k, v in data.items():
        if isinstance(v, str):
            result[k] = VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), v)
        else:
            result[k] = v
    return result
