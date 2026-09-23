# -*- coding: utf-8 -*-
"""
mimo_tools.base —— 工具抽象层

把不同来源的「能力」统一成同一种形状，让模型看到的永远是一份 OpenAI tools 规范：

    来源          实现文件        说明
    ----------    ------------    ------------------------------------------
    builtin       builtin.py      进程内直接调用的 Python 函数
    skill         skills.py       本地 skills/ 目录里的代码技能
    mcp           mcp.py          外部 MCP Server 暴露的工具

新增一种来源时，只需要：
  1. 实现一个能产出 Tool 对象的加载器
  2. 把 Tool 注册进 ToolRegistry
其余部分（模型调用、界面展示、命令行的 /tools）都不需要改。
"""

from __future__ import annotations

import inspect
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

__all__ = [
    "ToolResult",
    "Tool",
    "ToolRegistry",
    "ToolError",
    "sanitize_tool_name",
    "normalize_schema",
]

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


class ToolError(Exception):
    """工具执行失败。"""


def sanitize_tool_name(name: str) -> str:
    """把任意名字转成符合 OpenAI tools 命名规范的名字（1-64 位字母数字下划线连字符）。"""
    cleaned = _NAME_RE.sub("_", str(name or "").strip())
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "tool"
    return cleaned[:64]


def normalize_schema(parameters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """保证 parameters 是一份合法的 JSON Schema。"""
    if not isinstance(parameters, dict) or not parameters:
        return {"type": "object", "properties": {}}
    schema = dict(parameters)
    schema.setdefault("type", "object")
    if schema["type"] == "object":
        schema.setdefault("properties", {})
    return schema


@dataclass
class ToolResult:
    """工具执行结果。content 是要回灌给模型的文本。"""

    ok: bool
    content: str
    raw: Any = None
    elapsed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "content": self.content, "elapsed": round(self.elapsed, 3)}


@dataclass
class Tool:
    """一个可被模型调用的工具。"""

    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]
    source: str = "builtin"
    meta: Dict[str, Any] = field(default_factory=dict)
    # 只做展示、不给模型看的补充说明（例如来源文件路径）
    origin: str = ""

    def __post_init__(self) -> None:
        self.name = sanitize_tool_name(self.name)
        self.description = (self.description or "").strip() or self.name
        self.parameters = normalize_schema(self.parameters)

    def schema(self) -> Dict[str, Any]:
        """转成 OpenAI tools 规范里的一项。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "origin": self.origin,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """工具注册表：收集、查询、执行。"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self.notes: List[str] = []  # 加载过程中的提示 / 警告，供界面展示

    # ------------------------------------------------------------ 注册 --

    def register(self, tool: Tool, namespace: str = "") -> str:
        """
        注册一个工具，返回最终生效的名字。

        重名时自动加命名空间前缀（如 filesystem__read_file），避免覆盖已有工具。
        """
        name = tool.name
        if name in self._tools:
            prefix = sanitize_tool_name(namespace or tool.source or "x")
            candidate = sanitize_tool_name(f"{prefix}__{name}")
            self.notes.append(f"工具名 {name} 已存在，{tool.origin or tool.source} 自动改名为 {candidate}")
            name = candidate
            suffix = 2
            while name in self._tools:
                name = sanitize_tool_name(f"{candidate}_{suffix}")
                suffix += 1
        tool.name = name
        self._tools[name] = tool
        return name

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def note(self, message: str) -> None:
        self.notes.append(message)

    # ------------------------------------------------------------ 查询 --

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def all(self) -> List[Tool]:
        return [self._tools[n] for n in self.names()]

    def filter(self, include: Optional[Iterable[str]] = None, exclude: Optional[Iterable[str]] = None) -> "ToolRegistry":
        """按白名单 / 黑名单裁剪出一个新的注册表。"""
        sub = ToolRegistry()
        include_set = set(include or [])
        exclude_set = set(exclude or [])
        for tool in self.all():
            if include_set and tool.name not in include_set:
                continue
            if tool.name in exclude_set:
                continue
            sub._tools[tool.name] = tool
        sub.notes = list(self.notes)
        return sub

    def schemas(self) -> List[Dict[str, Any]]:
        return [tool.schema() for tool in self.all()]

    def describe(self) -> List[Dict[str, Any]]:
        return [tool.describe() for tool in self.all()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    # ------------------------------------------------------------ 执行 --

    def call(self, name: str, arguments: Any) -> ToolResult:
        """执行工具。任何异常都会被收敛成 ok=False 的结果，不会中断对话。"""
        started = time.time()
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                content=f"错误：不存在名为 {name} 的工具。可用工具：{', '.join(self.names()) or '（无）'}",
                elapsed=time.time() - started,
            )

        if isinstance(arguments, str):
            raw = arguments.strip()
            if not raw:
                args: Dict[str, Any] = {}
            else:
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return ToolResult(False, f"错误：工具参数不是合法 JSON（{exc}）。收到：{raw[:200]}", elapsed=time.time() - started)
                args = parsed if isinstance(parsed, dict) else {"value": parsed}
        elif isinstance(arguments, dict):
            args = arguments
        elif arguments is None:
            args = {}
        else:
            args = {"value": arguments}

        try:
            result = self._invoke(tool.handler, args)
        except ToolError as exc:
            return ToolResult(False, f"错误：{exc}", elapsed=time.time() - started)
        except TypeError as exc:
            return ToolResult(False, f"错误：工具参数不匹配（{exc}）", elapsed=time.time() - started)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"错误：工具执行失败（{type(exc).__name__}: {exc}）", elapsed=time.time() - started)

        elapsed = time.time() - started
        if isinstance(result, ToolResult):
            result.elapsed = elapsed
            return result
        return ToolResult(True, self._stringify(result), raw=result, elapsed=elapsed)

    @staticmethod
    def _invoke(handler: Callable[..., Any], args: Dict[str, Any]) -> Any:
        """只把工具函数声明过的参数传进去，多余的键直接忽略，避免模型多给参数就报错。"""
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            return handler(**args)

        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        if accepts_kwargs:
            return handler(**args)
        allowed = {k: v for k, v in args.items() if k in signature.parameters}
        return handler(**allowed)

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return "（无返回值）"
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple)):
            try:
                return json.dumps(value, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                return str(value)
        return str(value)
