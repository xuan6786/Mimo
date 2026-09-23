# -*- coding: utf-8 -*-
"""
mimo_tools.skills —— 本地技能加载器

技能放在 skills/ 目录下，一个技能一个文件夹（也支持单个 .py 文件）。

支持两类技能：

  1) 代码技能 —— 会被注册成模型可调用的工具
     skills/<名字>/skill.py
         TOOL = {
             "name": "word_count",
             "description": "统计文本字数",
             "parameters": {"type": "object",
                            "properties": {"text": {"type": "string", "description": "待统计文本"}},
                            "required": ["text"]},
         }
         def run(text: str) -> str:
             return f"共 {len(text)} 个字符"

     TOOL 也可以是一个列表，一次暴露多个工具。此时入口函数默认取与工具同名的函数，
     也可以用 handler 字段显式指定：
         TOOL = [
             {"name": "json_format", "description": "...", "parameters": {...}},
             {"name": "json_query",  "description": "...", "parameters": {...}, "handler": "query"},
         ]
         def json_format(text): ...   # 名字与工具名一致，自动匹配
         def query(text, path): ...   # 由 handler 指定

  2) 提示词技能 —— 不产生工具，启用后把 SKILL.md 正文追加进系统提示词
     skills/<名字>/SKILL.md
         ---
         name: 代码审查
         description: 审查代码时遵循的规范
         ---
         （这里写具体的要求，会被拼进系统提示词）

SKILL.md 是可选的：代码技能没有它也能工作，只是少一份人类可读的说明。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from .base import Tool, ToolRegistry, sanitize_tool_name

__all__ = ["SkillLoader", "SkillBundle", "parse_skill_md"]

SKILL_FILE = "SKILL.md"
CODE_FILE = "skill.py"
ENTRY_FUNCTION = "run"


class SkillBundle:
    """一次技能扫描的结果。"""

    def __init__(self) -> None:
        self.tools: List[Tool] = []
        self.prompts: List[Dict[str, str]] = []
        self.notes: List[str] = []

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SkillBundle tools={len(self.tools)} prompts={len(self.prompts)}>"


def parse_skill_md(text: str) -> Tuple[Dict[str, str], str]:
    """
    解析 SKILL.md：返回 (frontmatter 键值对, 正文)。

    只支持扁平的 `key: value`，不引入 YAML 依赖。没有 frontmatter 时返回 ({}, 全文)。
    """
    raw = (text or "").replace("\r\n", "\n")
    if not raw.lstrip().startswith("---"):
        return {}, raw.strip()

    lines = raw.lstrip().split("\n")
    meta: Dict[str, str] = {}
    body_start = None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body_start = index + 1
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip().strip("'\"")
    if body_start is None:
        return meta, ""
    return meta, "\n".join(lines[body_start:]).strip()


class SkillLoader:
    def __init__(
        self,
        skills_dir: str,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
    ):
        self.skills_dir = os.path.abspath(skills_dir)
        self.include = [sanitize_tool_name(n) for n in (include or [])]
        self.exclude = [sanitize_tool_name(n) for n in (exclude or [])]

    # ------------------------------------------------------------ 扫描 --

    def discover(self) -> List[str]:
        """列出候选技能名（目录名或 .py 文件名）。"""
        if not os.path.isdir(self.skills_dir):
            return []
        names = []
        for entry in sorted(os.listdir(self.skills_dir)):
            full = os.path.join(self.skills_dir, entry)
            if entry.startswith((".", "_")):
                continue
            if os.path.isdir(full):
                if os.path.isfile(os.path.join(full, CODE_FILE)) or os.path.isfile(os.path.join(full, SKILL_FILE)):
                    names.append(entry)
            elif entry.endswith(".py") and entry != CODE_FILE:
                names.append(entry[:-3])
        return names

    def load(self) -> SkillBundle:
        bundle = SkillBundle()
        if not os.path.isdir(self.skills_dir):
            return bundle

        for name in self.discover():
            key = sanitize_tool_name(name)
            if self.include and key not in self.include and name not in self.include:
                continue
            if key in self.exclude or name in self.exclude:
                continue
            try:
                self._load_one(name, bundle)
            except Exception as exc:  # noqa: BLE001
                bundle.notes.append(f"技能 {name} 加载失败：{type(exc).__name__}: {exc}")
        return bundle

    # ------------------------------------------------------------ 单个 --

    def _load_one(self, name: str, bundle: SkillBundle) -> None:
        folder = os.path.join(self.skills_dir, name)
        if os.path.isdir(folder):
            code_path = os.path.join(folder, CODE_FILE)
            md_path = os.path.join(folder, SKILL_FILE)
        else:
            code_path = os.path.join(self.skills_dir, name + ".py")
            md_path = os.path.join(self.skills_dir, name + ".md")

        meta: Dict[str, str] = {}
        body = ""
        if os.path.isfile(md_path):
            with open(md_path, "r", encoding="utf-8", errors="replace") as fp:
                meta, body = parse_skill_md(fp.read())

        if not os.path.isfile(code_path):
            # 纯提示词技能
            if not body:
                bundle.notes.append(f"技能 {name} 既没有 {CODE_FILE} 也没有可用的 {SKILL_FILE}，已跳过")
                return
            bundle.prompts.append(
                {
                    "name": meta.get("name") or name,
                    "key": sanitize_tool_name(name),
                    "description": meta.get("description") or body.split("\n")[0][:120],
                    "body": body,
                    "path": md_path,
                }
            )
            return

        module = self._import_module(code_path, name)
        tool_def = getattr(module, "TOOL", None)

        if isinstance(tool_def, dict):
            definitions = [tool_def]
        elif isinstance(tool_def, list):
            definitions = [d for d in tool_def if isinstance(d, dict)]
        else:
            definitions = []

        if not definitions:
            # 没写 TOOL 时，退化成「一个以文件夹命名的工具」，入口仍然是 run()
            handler = getattr(module, ENTRY_FUNCTION, None)
            if not callable(handler):
                bundle.notes.append(f"技能 {name} 的 {CODE_FILE} 里既没有 TOOL 定义也没有 {ENTRY_FUNCTION}() 函数，已跳过")
                return
            definitions = [
                {
                    "name": name,
                    "description": (meta.get("description") or (body.split("\n")[0] if body else "") or name)[:500],
                    "parameters": {"type": "object", "properties": {}},
                    "handler": ENTRY_FUNCTION,
                }
            ]

        for definition in definitions:
            tool_name = definition.get("name") or name
            # 列表形式下，入口函数默认取工具名同名函数；也可用 handler 字段显式指定
            default_entry = tool_name if len(definitions) > 1 else ENTRY_FUNCTION
            entry = definition.get("handler") or definition.get("entry") or default_entry
            handler = getattr(module, entry, None)
            if not callable(handler):
                bundle.notes.append(f"技能 {name} 里的工具 {tool_name} 找不到可调用的入口函数 {entry}()，已跳过")
                continue
            bundle.tools.append(
                Tool(
                    name=tool_name,
                    description=definition.get("description") or meta.get("description") or tool_name,
                    parameters=definition.get("parameters") or {"type": "object", "properties": {}},
                    handler=handler,
                    source="skill",
                    origin=os.path.relpath(code_path, os.path.dirname(self.skills_dir)),
                    meta={"skill": name, "dir": folder if os.path.isdir(folder) else self.skills_dir},
                )
            )

    @staticmethod
    def _import_module(path: str, name: str):
        module_name = "mimo_skill_" + sanitize_tool_name(name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块：{path}")
        module = importlib.util.module_from_spec(spec)

        folder = os.path.dirname(path)
        added = folder not in sys.path
        if added:
            sys.path.insert(0, folder)
        try:
            spec.loader.exec_module(module)
        finally:
            if added and folder in sys.path:
                sys.path.remove(folder)
        return module

    # ------------------------------------------------------------ 汇总 --

    def register_into(self, registry: ToolRegistry) -> SkillBundle:
        """把技能注册进工具表，并返回扫描结果（提示词技能由调用方处理）。"""
        bundle = self.load()
        for tool in bundle.tools:
            registry.register(tool, namespace=tool.meta.get("skill") or "skill")
        for note in bundle.notes:
            registry.note(note)
        return bundle
