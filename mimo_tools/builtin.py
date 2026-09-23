# -*- coding: utf-8 -*-
"""
mimo_tools.builtin —— 内置工具

这些工具不依赖任何外部服务，用来验证整条工具调用链路，也可以直接当日常小工具用。

安全约定：
  * 所有文件操作都被限制在 sandbox_root（默认是项目目录）之内，越界直接拒绝
  * 写文件默认关闭，需要在 config.json 里显式打开 tools.builtin.allow_write
  * 联网默认开启，可用 tools.builtin.allow_network 关闭
"""

from __future__ import annotations

import datetime as _dt
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .base import Tool, ToolError

__all__ = ["build_builtin_tools"]

# 单次读取 / 返回的字符上限，防止把上下文撑爆
DEFAULT_MAX_CHARS = 20000


def _resolve(root: str, path: str) -> str:
    """把用户给的路径解析到 root 之内，越界就报错。"""
    raw = (path or ".").strip() or "."
    if os.path.isabs(raw):
        candidate = os.path.normpath(raw)
    else:
        candidate = os.path.normpath(os.path.join(root, raw))

    root_norm = os.path.normpath(root)
    try:
        common = os.path.commonpath([root_norm, candidate])
    except ValueError:  # 不同盘符
        common = ""
    if common != root_norm:
        raise ToolError(
            f"路径 {path} 超出了允许访问的目录范围（{root_norm}）。"
            "如需访问其他位置，请修改 config.json 里的 tools.sandbox_root。"
        )
    return candidate


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（内容过长，已截断，共 {len(text)} 字符，仅显示前 {limit} 字符）"


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #

def build_builtin_tools(
    root: str,
    allow_write: bool = False,
    allow_network: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> List[Tool]:
    root = os.path.abspath(root)

    # ------------------------------------------------------------ 时间 --

    def get_current_time(timezone_offset: Optional[float] = None) -> str:
        now = _dt.datetime.now()
        if timezone_offset is not None:
            now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=float(timezone_offset))
        week = "一二三四五六日"[now.weekday()]
        return (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} 星期{week}\n"
            f"（{'' if timezone_offset is None else f'UTC{float(timezone_offset):+g} '}时区，格式 YYYY-MM-DD HH:MM:SS）"
        )

    # ---------------------------------------------------------- 目录列表 --

    def list_directory(path: str = ".", show_hidden: bool = False) -> str:
        target = _resolve(root, path)
        if not os.path.isdir(target):
            raise ToolError(f"{path} 不是一个目录")
        entries = []
        for name in sorted(os.listdir(target)):
            if not show_hidden and name.startswith("."):
                continue
            full = os.path.join(target, name)
            try:
                if os.path.isdir(full):
                    entries.append(f"[目录] {name}/")
                else:
                    entries.append(f"[文件] {name}  ({os.path.getsize(full)} 字节)")
            except OSError:
                entries.append(f"[未知] {name}")
        if not entries:
            return f"目录 {path} 为空。"
        return f"目录 {path} 下共 {len(entries)} 项：\n" + "\n".join(entries)

    # ------------------------------------------------------------ 读文件 --

    def read_text_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
        target = _resolve(root, path)
        if not os.path.isfile(target):
            raise ToolError(f"文件不存在：{path}")
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as fp:
                lines = fp.readlines()
        except OSError as exc:
            raise ToolError(f"读取失败：{exc}") from None

        start = max(1, int(start_line or 1))
        end = int(end_line or 0)
        chunk = lines[start - 1: end if end >= start else None]
        body = "".join(chunk)
        header = f"文件 {path}，共 {len(lines)} 行" + (f"，当前显示第 {start}-{min(end, len(lines)) if end else len(lines)} 行" if len(lines) > len(chunk) else "")
        return header + "：\n" + _truncate(body, max_chars)

    # ------------------------------------------------------------ 写文件 --

    def write_text_file(path: str, content: str, mode: str = "create") -> str:
        if not allow_write:
            raise ToolError("写文件能力未开启（config.json → tools.builtin.allow_write 设为 true 后可用）")
        target = _resolve(root, path)
        if os.path.isdir(target):
            raise ToolError(f"{path} 是一个目录，不能写入")
        if mode == "create" and os.path.exists(target):
            raise ToolError(f"文件已存在：{path}。如需覆盖请把 mode 设为 overwrite，如需追加设为 append")
        if mode not in ("create", "overwrite", "append"):
            raise ToolError("mode 只能是 create / overwrite / append")

        os.makedirs(os.path.dirname(target) or root, exist_ok=True)
        with open(target, "a" if mode == "append" else "w", encoding="utf-8") as fp:
            fp.write(content or "")
        size = os.path.getsize(target)
        return f"已{mode}写入 {path}，当前文件大小 {size} 字节。"

    # ------------------------------------------------------------ 联网 --

    def http_get(url: str, max_length: int = 6000) -> str:
        if not allow_network:
            raise ToolError("联网能力未开启（config.json → tools.builtin.allow_network 设为 true 后可用）")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolError("只支持 http / https 协议")
        request = urllib.request.Request(url, headers={"User-Agent": "MiMo-Local-Assistant/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                raw = resp.read(200000)
            text = raw.decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            raise ToolError(f"请求返回 HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"请求失败：{exc}") from None
        return f"GET {url} → {len(text)} 字符\n\n" + _truncate(text, int(max_length or 6000))

    # ------------------------------------------------------------ 注册 --

    return [
        Tool(
            name="get_current_time",
            description="获取当前日期和时间。当问题涉及「今天」「现在」「本周」等相对时间时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "timezone_offset": {
                        "type": "number",
                        "description": "相对 UTC 的时区偏移小时数，例如北京时间为 8。不传则用本机时区。",
                    }
                },
            },
            handler=get_current_time,
            source="builtin",
            origin="mimo_tools/builtin.py",
        ),
        Tool(
            name="list_directory",
            description="列出某个目录下的文件和子目录。只能访问被授权的沙箱目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的路径，默认为当前目录 '.'"},
                    "show_hidden": {"type": "boolean", "description": "是否显示以点开头的隐藏文件，默认 false"},
                },
            },
            handler=list_directory,
            source="builtin",
            origin="mimo_tools/builtin.py",
        ),
        Tool(
            name="read_text_file",
            description="读取沙箱目录内某个文本文件的内容，可按行号范围截取。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的文件路径"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
                    "end_line": {"type": "integer", "description": "结束行号（含），0 表示读到文件末尾"},
                },
                "required": ["path"],
            },
            handler=read_text_file,
            source="builtin",
            origin="mimo_tools/builtin.py",
        ),
        Tool(
            name="write_text_file",
            description="在沙箱目录内写入文本文件。需要管理员在配置中开启写权限后可用。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对沙箱根目录的文件路径"},
                    "content": {"type": "string", "description": "要写入的文本内容"},
                    "mode": {
                        "type": "string",
                        "enum": ["create", "overwrite", "append"],
                        "description": "create 仅新建（默认，文件已存在则失败）、overwrite 覆盖、append 追加",
                    },
                },
                "required": ["path", "content"],
            },
            handler=write_text_file,
            source="builtin",
            origin="mimo_tools/builtin.py",
            meta={"dangerous": True},
        ),
        Tool(
            name="http_get",
            description="抓取一个 http/https 网址的文本内容，用于获取网页或接口返回的数据。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的 http 或 https 地址"},
                    "max_length": {"type": "integer", "description": "最多返回的字符数，默认 6000"},
                },
                "required": ["url"],
            },
            handler=http_get,
            source="builtin",
            origin="mimo_tools/builtin.py",
        ),
    ]
