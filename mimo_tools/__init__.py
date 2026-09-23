# -*- coding: utf-8 -*-
"""
mimo_tools —— MiMo 助手的工具扩展层

一条统一的能力总线，三个来源：

    builtin  进程内 Python 函数
    skill    本地 skills/ 目录（代码技能 → 工具；提示词技能 → 系统提示词）
    mcp      外部 MCP Server（stdio / http）

对外只需要两个入口：

    ctx = build_tools(config, base_dir)     # 按配置装配出全部工具
    runner = AgentRunner(client, ctx.registry, ctx.max_iterations)
    for event in runner.run(messages): ...

要新增一类来源，写一个加载器把 Tool 注册进 ToolRegistry 即可，其余代码不用动。
"""

from __future__ import annotations

import atexit
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .agent import AgentRunner
from .base import Tool, ToolError, ToolRegistry, ToolResult, sanitize_tool_name
from .builtin import build_builtin_tools
from .mcp import McpError, McpManager, load_mcp_specs
from .skills import SkillBundle, SkillLoader

__all__ = [
    "AgentRunner",
    "Tool",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolContext",
    "build_tools",
    "sanitize_tool_name",
]

DEFAULT_TOOLS_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "max_iterations": 6,
    "sandbox_root": "",
    "builtin": {"enabled": True, "allow_write": False, "allow_network": True},
    "skills": {"enabled": True, "dir": "skills", "include": [], "exclude": []},
    "mcp": {"enabled": True, "config": "mcp.json", "include": [], "exclude": []},
}


def merge_tools_config(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把用户配置合并到默认值上，缺字段不会炸。"""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_TOOLS_CONFIG.items()}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


@dataclass
class ToolContext:
    """一次装配的结果，同时持有需要显式关闭的资源（MCP 子进程）。"""

    enabled: bool = False
    max_iterations: int = 6
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    prompts: List[Dict[str, str]] = field(default_factory=list)
    mcp: Optional[McpManager] = None
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # 没开启工具时也能告诉用户「开了能得到什么」
    catalog: Dict[str, List[str]] = field(default_factory=lambda: {"skills": [], "mcp": []})

    @property
    def tool_count(self) -> int:
        return len(self.registry)

    def describe(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_iterations": self.max_iterations,
            "tools": self.registry.describe(),
            "prompts": [
                {"name": p.get("name"), "description": p.get("description"), "path": p.get("path") or p.get("origin", "")}
                for p in self.prompts
            ],
            "catalog": self.catalog,
            "notes": self.notes,
            "errors": self.errors,
        }

    def system_prompt_suffix(self) -> str:
        """把提示词技能与 MCP 使用说明拼成一段可直接追加到系统提示词的文本。"""
        blocks = [p for p in self.prompts if p.get("body")]
        if not blocks:
            return ""
        parts = ["\n\n---\n\n# 可用能力说明\n"]
        if self.registry:
            parts.append("你可以调用以下工具来获取信息或执行操作：" + "、".join(self.registry.names()) + "。\n")
        for block in blocks:
            parts.append(f"\n## {block.get('name')}\n\n{block['body'].strip()}\n")
        return "".join(parts)

    def close(self) -> None:
        if self.mcp is not None:
            self.mcp.close_all()
            self.mcp = None


def _resolve_dir(path: str, base_dir: str) -> str:
    """相对路径按 base_dir 解析。"""
    return path if os.path.isabs(path) else os.path.join(base_dir, path)


def build_tools(config: Optional[Dict[str, Any]], base_dir: str) -> ToolContext:
    """
    按 config["tools"] 装配工具。

    base_dir 是项目目录，用于解析 skills 目录、mcp.json 与默认沙箱根目录。
    """
    cfg = merge_tools_config((config or {}).get("tools"))
    ctx = ToolContext(enabled=bool(cfg.get("enabled")), max_iterations=int(cfg.get("max_iterations") or 6))
    base_dir = os.path.abspath(base_dir)

    if not ctx.enabled:
        # 没开启也要能列出「开了能得到什么」，方便决定要不要打开
        skills_cfg = cfg.get("skills") or {}
        if skills_cfg.get("enabled", True):
            skills_dir = _resolve_dir(skills_cfg.get("dir") or "skills", base_dir)
            if os.path.isdir(skills_dir):
                ctx.catalog["skills"] = SkillLoader(
                    skills_dir,
                    include=skills_cfg.get("include") or [],
                    exclude=skills_cfg.get("exclude") or [],
                ).discover()
        mcp_cfg = cfg.get("mcp") or {}
        if mcp_cfg.get("enabled", True):
            mcp_path = _resolve_dir(mcp_cfg.get("config") or "mcp.json", base_dir)
            if os.path.isfile(mcp_path):
                try:
                    specs = load_mcp_specs(mcp_path)
                    ctx.catalog["mcp"] = sorted(name for name, spec in specs.items() if spec.enabled)
                except McpError as exc:
                    ctx.errors.append(str(exc))
        return ctx

    registry = ctx.registry

    # ------------------------------------------------------------ builtin --
    builtin_cfg = cfg.get("builtin") or {}
    if builtin_cfg.get("enabled", True):
        sandbox = str(cfg.get("sandbox_root") or "").strip()
        sandbox = os.path.abspath(sandbox) if sandbox else base_dir
        if not os.path.isdir(sandbox):
            ctx.errors.append(f"沙箱目录不存在：{sandbox}，已回退到项目目录 {base_dir}")
            sandbox = base_dir
        for tool in build_builtin_tools(
            root=sandbox,
            allow_write=bool(builtin_cfg.get("allow_write", False)),
            allow_network=bool(builtin_cfg.get("allow_network", True)),
        ):
            registry.register(tool)
        registry.note(f"内置工具已加载，沙箱根目录：{sandbox}")

    # ------------------------------------------------------------- skills --
    skills_cfg = cfg.get("skills") or {}
    if skills_cfg.get("enabled", True):
        skills_dir = _resolve_dir(str(skills_cfg.get("dir") or "skills"), base_dir)
        if os.path.isdir(skills_dir):
            loader = SkillLoader(
                skills_dir,
                include=skills_cfg.get("include") or [],
                exclude=skills_cfg.get("exclude") or [],
            )
            ctx.catalog["skills"] = loader.discover()
            bundle: SkillBundle = loader.register_into(registry)
            for prompt in bundle.prompts:
                ctx.prompts.append(
                    {
                        "name": prompt.get("name") or prompt.get("key"),
                        "description": prompt.get("description") or "",
                        "body": prompt.get("body") or "",
                        "path": prompt.get("path") or "",
                    }
                )
            if bundle.tools or bundle.prompts:
                registry.note(f"已加载技能 {len(bundle.tools)} 个（代码）+ {len(bundle.prompts)} 个（提示词）")
        else:
            registry.note(f"技能目录不存在，已跳过：{skills_dir}")

    # ---------------------------------------------------------------- mcp --
    mcp_cfg = cfg.get("mcp") or {}
    if mcp_cfg.get("enabled", True):
        mcp_path = _resolve_dir(str(mcp_cfg.get("config") or "mcp.json"), base_dir)
        if os.path.isfile(mcp_path):
            manager = McpManager()
            try:
                specs = load_mcp_specs(mcp_path)
                enabled_specs = {k: v for k, v in specs.items() if v.enabled}
                ctx.catalog["mcp"] = sorted(enabled_specs)
                if enabled_specs:
                    manager.connect_all(
                        specs,
                        include=mcp_cfg.get("include") or [],
                        exclude=mcp_cfg.get("exclude") or [],
                    )
                    manager.bridge(registry)
                    for prompt in manager.prompt_blocks():
                        ctx.prompts.append(
                            {"name": prompt["name"], "description": "MCP Server 使用说明", "body": prompt["body"], "path": mcp_path}
                        )
                ctx.mcp = manager
                ctx.notes.extend(manager.notes)
                ctx.errors.extend(manager.errors)
                # 兜底：进程退出时确保 MCP 子进程被回收
                atexit.register(manager.close_all)
            except McpError as exc:
                ctx.errors.append(str(exc))
                manager.close_all()
        else:
            registry.note(f"未找到 MCP 配置文件，已跳过：{mcp_path}")

    ctx.notes = list(registry.notes) + ctx.notes
    return ctx
