# -*- coding: utf-8 -*-
"""
assistant.py —— 本地 MiMo 对话助手（命令行版）

零第三方依赖，只用 Python 标准库。

用法：
    python assistant.py                     # 进入交互式对话
    python assistant.py -p "你好"            # 单次问答后退出（便于脚本调用）
    python assistant.py --check             # 连通性自检
    python assistant.py --model mimo-v2.6-flash
    python assistant.py --config other.json

API Key 读取优先级：命令行 --api-key > 环境变量 MIMO_API_KEY > config.json 的 api_key
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mimo_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    KNOWN_MODELS,
    MiMoClient,
    MiMoError,
)
from mimo_tools import AgentRunner, build_tools  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SESSION_DIR = os.path.join(APP_DIR, "sessions")
VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# 终端着色
# --------------------------------------------------------------------------- #

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def enable_ansi() -> None:
    """在 Windows 控制台开启 ANSI 转义支持。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def disable_color() -> None:
    for name in dir(C):
        if name.isupper():
            setattr(C, name, "")


def use_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "sk-c9ftiqakm1c852vk5as9nq6m0od2yayygydgnskh50e06wza",
    "base_url": DEFAULT_BASE_URL,
    "model": DEFAULT_MODEL,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "stream": True,
    "show_reasoning": True,
    "timeout": 180,
    "max_retries": 3,
    "auth_scheme": "both",
    "proxy": "",
    "params": {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_completion_tokens": 4096,
    },
    "extra_body": {},
}


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fp:
                user_cfg = json.load(fp)
            if isinstance(user_cfg, dict):
                for key, value in user_cfg.items():
                    if key in ("params", "extra_body") and isinstance(value, dict):
                        cfg.setdefault(key, {}).update(value)
                    else:
                        cfg[key] = value
        except json.JSONDecodeError as exc:
            print(f"{C.YELLOW}⚠ 配置文件 {path} 解析失败（{exc}），已改用默认配置。{C.RESET}")
    return cfg


def save_config(cfg: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)


def mask_key(key: str) -> str:
    if not key:
        return "(未设置)"
    if len(key) <= 10:
        return key[:2] + "*" * max(len(key) - 2, 1)
    return f"{key[:6]}{'*' * 8}{key[-4:]}"


def build_system_prompt(template: str) -> str:
    now = _dt.datetime.now()
    week = "星期" + "一二三四五六日"[now.weekday()]
    try:
        return template.format(date=now.strftime("%Y年%m月%d日"), week=week)
    except (KeyError, IndexError, ValueError):
        return template


# --------------------------------------------------------------------------- #
# 对话会话
# --------------------------------------------------------------------------- #

class Session:
    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.messages: List[Dict[str, Any]] = []
        self.usage_total: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.turns = 0

    # -- 上下文 ---------------------------------------------------------- #

    def api_messages(self) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.extend(self.messages)
        return msgs

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, content: str, reasoning: str = "") -> None:
        msg: Dict[str, Any] = {"role": "assistant", "content": content}
        # 官方建议：多轮工具调用时保留历史 reasoning_content
        if reasoning:
            msg["reasoning_content"] = reasoning
        self.messages.append(msg)

    def undo_last_turn(self) -> int:
        """删掉最后一条 assistant 及其前面的 user，返回删除条数。"""
        removed = 0
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages.pop()
            removed += 1
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
            removed += 1
        self.turns = max(0, self.turns - 1)
        return removed

    def accumulate_usage(self, usage: Dict[str, Any]) -> None:
        for key in self.usage_total:
            try:
                self.usage_total[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass

    def reset(self) -> None:
        self.messages.clear()
        self.turns = 0

    # -- 持久化 ---------------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "saved_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "usage_total": self.usage_total,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        sess = cls(data.get("system_prompt") or "")
        sess.messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        sess.usage_total.update(data.get("usage_total") or {})
        sess.turns = sum(1 for m in sess.messages if m.get("role") == "user")
        return sess


def list_sessions() -> List[str]:
    if not os.path.isdir(SESSION_DIR):
        return []
    files = [f for f in os.listdir(SESSION_DIR) if f.endswith(".json")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(SESSION_DIR, f)), reverse=True)
    return files


def save_session(sess: Session, name: str) -> str:
    os.makedirs(SESSION_DIR, exist_ok=True)
    safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip() or "session"
    if not safe.endswith(".json"):
        safe += ".json"
    path = os.path.join(SESSION_DIR, safe)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(sess.to_dict(), fp, ensure_ascii=False, indent=2)
    return path


def load_session(name: str) -> Session:
    if not name.endswith(".json"):
        name += ".json"
    path = os.path.join(SESSION_DIR, name)
    with open(path, "r", encoding="utf-8") as fp:
        return Session.from_dict(json.load(fp))


# --------------------------------------------------------------------------- #
# 助手主体
# --------------------------------------------------------------------------- #

HELP_TEXT = f"""
{C.BOLD}{C.CYAN}── MiMo 本地助手 · 命令一览 ──{C.RESET}

{C.GREEN}/help{C.RESET}                 显示本帮助
{C.GREEN}/new{C.RESET}                  开启新对话（清空上下文）
{C.GREEN}/model [名称]{C.RESET}         查看或切换模型，例：/model mimo-v2.6-flash
{C.GREEN}/models{C.RESET}               列出可用模型
{C.GREEN}/system [内容]{C.RESET}        查看或设置系统提示词（角色设定）
{C.GREEN}/tools [on|off]{C.RESET}       查看已装配的工具（内置 / 技能 / MCP），或开关工具调用
{C.GREEN}/tool 名字 {{json}}{C.RESET}    手动执行一个工具，例：/tool get_current_time {{}}
{C.GREEN}/set 键=值{C.RESET}            设置请求参数，例：/set temperature=0.6
{C.GREEN}/unset 键{C.RESET}             移除某个请求参数
{C.GREEN}/params{C.RESET}               查看当前请求参数
{C.GREEN}/stream on|off{C.RESET}        开/关流式输出
{C.GREEN}/think on|off{C.RESET}         开/关思考过程显示
{C.GREEN}/history{C.RESET}              查看当前对话上下文
{C.GREEN}/undo{C.RESET}                 撤销上一轮问答
{C.GREEN}/retry{C.RESET}                重新生成上一次回复
{C.GREEN}/save [文件名]{C.RESET}        保存当前对话到 sessions/
{C.GREEN}/load [文件名]{C.RESET}        载入已保存的对话
{C.GREEN}/sessions{C.RESET}             列出所有已保存对话
{C.GREEN}/usage{C.RESET}                查看本会话累计 token 用量
{C.GREEN}/config{C.RESET}               查看当前生效配置
{C.GREEN}/clear{C.RESET}                清屏
{C.GREEN}/exit{C.RESET} 或 {C.GREEN}/quit{C.RESET}      退出

{C.DIM}多行输入：行尾加反斜杠 \\ 可换行继续输入。Ctrl+C 可中断正在生成的回复。{C.RESET}
"""


class Assistant:
    def __init__(self, cfg: Dict[str, Any], config_path: str):
        self.cfg = cfg
        self.config_path = config_path
        self.params: Dict[str, Any] = dict(cfg.get("params") or {})
        self.extra_body: Dict[str, Any] = dict(cfg.get("extra_body") or {})
        self.stream: bool = bool(cfg.get("stream", True))
        self.show_reasoning: bool = bool(cfg.get("show_reasoning", True))
        self.base_system_prompt: str = build_system_prompt(cfg.get("system_prompt") or "")

        # 工具扩展层：builtin / skill / mcp 在启动时统一装配
        self.tool_ctx = build_tools(cfg, APP_DIR)
        self._runner: Optional[AgentRunner] = None

        self.session = Session(self._compose_system_prompt())
        self.last_answer: str = ""
        self.last_reasoning: str = ""
        self.pending_user: str = ""
        self._client: Optional[MiMoClient] = None

    # ------------------------------------------------------------ 工具 --

    def _compose_system_prompt(self) -> str:
        """基础系统提示词 + 提示词技能 / MCP 使用说明。"""
        suffix = self.tool_ctx.system_prompt_suffix() if self.tool_ctx else ""
        return (self.base_system_prompt or "") + suffix

    @property
    def runner(self) -> Optional[AgentRunner]:
        """工具全部装配好、且确实有可用工具时才走 Agent 循环。"""
        if self.tool_ctx is None or not self.tool_ctx.enabled or self.tool_ctx.tool_count == 0:
            return None
        if self._runner is None:
            self._runner = AgentRunner(self.client, self.tool_ctx.registry, self.tool_ctx.max_iterations)
        return self._runner

    def _run_events(self, messages: List[Dict[str, Any]]):
        """统一的事件源：有工具走 Agent 循环，没有就直接问模型。"""
        runner = self.runner
        if runner is not None:
            return runner.run(messages, params=self.params, extra_body=self.extra_body, stream=self.stream)
        return self.client.chat(messages, stream=self.stream, params=self.params, extra_body=self.extra_body)

    def _print_tools(self) -> None:
        ctx = self.tool_ctx
        if ctx is None:
            print(f"{C.YELLOW}工具扩展层未初始化。{C.RESET}")
            return
        if not ctx.enabled or ctx.tool_count == 0:
            print(
                f"{C.YELLOW}工具调用未开启。{C.RESET} 用 {C.BOLD}/tools on{C.RESET} 临时开启，"
                f"或把 config.json 里的 tools.enabled 设为 true。"
            )
            catalog = ctx.catalog or {}
            skills = catalog.get("skills") or []
            mcp = catalog.get("mcp") or []
            if skills:
                print(f"  {C.GREEN}检测到技能：{C.RESET}{'、'.join(skills)}")
            if mcp:
                print(f"  {C.MAGENTA}检测到 MCP Server：{C.RESET}{'、'.join(mcp)}")
            if not skills and not mcp:
                print(f"  {C.GRAY}（skills/ 下暂无技能，mcp.json 里暂无启用的服务器）{C.RESET}")
            return
        state = f"{C.GREEN}已开启{C.RESET}" if ctx.enabled else f"{C.GRAY}已关闭{C.RESET}"
        print(f"{C.BOLD}工具扩展层：{state}{C.RESET}  共 {ctx.tool_count} 个工具，单次对话最多 {ctx.max_iterations} 轮调用")
        print(f"{C.BOLD}工具清单：{C.RESET}")
        for item in ctx.registry.describe():
            badge = {"builtin": C.BLUE, "skill": C.GREEN, "mcp": C.MAGENTA}.get(item["source"], C.GRAY)
            print(f"  {badge}[{item['source']:7}]{C.RESET} {C.BOLD}{item['name']}{C.RESET}")
            print(f"      {C.GRAY}{item['description'][:88]}{C.RESET}")
        if ctx.prompts:
            print(f"{C.BOLD}提示词技能（已注入系统提示词）：{C.RESET}")
            for prompt in ctx.prompts:
                print(f"  {C.GREEN}◆{C.RESET} {prompt['name']}  {C.GRAY}{str(prompt.get('description') or '')[:70]}{C.RESET}")
        for note in ctx.notes:
            print(f"  {C.GRAY}· {note}{C.RESET}")
        for err in ctx.errors:
            print(f"  {C.RED}! {err}{C.RESET}")
        print(f"{C.GRAY}手动执行：/tool 工具名 {{\"参数\": \"值\"}}{C.RESET}")

    def _invoke_tool(self, arg: str) -> None:
        ctx = self.tool_ctx
        if ctx is None or not ctx.enabled or ctx.tool_count == 0:
            print(f"{C.YELLOW}工具未启用，先执行 /tools on{C.RESET}")
            return
        parts = arg.split(maxsplit=1)
        if not parts:
            print(f'{C.YELLOW}用法：/tool 工具名 {{"参数": "值"}}{C.RESET}')
            return
        name = parts[0]
        raw = parts[1] if len(parts) > 1 else "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"{C.RED}✗ 参数不是合法 JSON：{exc}{C.RESET}")
            return
        result = ctx.registry.call(name, arguments)
        mark = f"{C.GREEN}✓{C.RESET}" if result.ok else f"{C.RED}✗{C.RESET}"
        print(f"{mark} {C.BOLD}{name}{C.RESET} {C.GRAY}（{result.elapsed:.3f}s）{C.RESET}")
        print(result.content)

    def close(self) -> None:
        """释放工具扩展层持有的资源（主要是 MCP 子进程）。"""
        if self.tool_ctx is not None:
            self.tool_ctx.close()

    # ------------------------------------------------------------ 客户端 --

    @property
    def client(self) -> MiMoClient:
        if self._client is None:
            self._client = MiMoClient(
                api_key=self.cfg.get("api_key") or os.environ.get("MIMO_API_KEY", ""),
                base_url=self.cfg.get("base_url") or DEFAULT_BASE_URL,
                model=self.cfg.get("model") or DEFAULT_MODEL,
                timeout=self.cfg.get("timeout", 180),
                max_retries=self.cfg.get("max_retries", 3),
                auth_scheme=self.cfg.get("auth_scheme", "both"),
                proxy=self.cfg.get("proxy") or None,
            )
        return self._client

    @property
    def model(self) -> str:
        return self.cfg.get("model") or DEFAULT_MODEL

    # ------------------------------------------------------------ 输出 --

    def _print_banner(self) -> None:
        key_state = f"{C.GREEN}已配置{C.RESET}" if (self.cfg.get("api_key") or os.environ.get("MIMO_API_KEY")) else f"{C.RED}未配置{C.RESET}"
        ctx = self.tool_ctx
        if ctx is not None and ctx.enabled and ctx.tool_count:
            sources = {}
            for item in ctx.registry.describe():
                sources[item["source"]] = sources.get(item["source"], 0) + 1
            detail = "、".join(f"{k}×{v}" for k, v in sorted(sources.items()))
            tool_state = f"{C.GREEN}已开启{C.RESET}（{ctx.tool_count} 个：{detail}）"
        else:
            tool_state = f"{C.GRAY}未开启{C.RESET}"
        print(f"""{C.CYAN}{C.BOLD}
  ╭──────────────────────────────────────────────╮
  │        MiMo 本地对话助手  v{VERSION}            │
  ╰──────────────────────────────────────────────╯{C.RESET}
  {C.GRAY}模型：{C.RESET}{C.BOLD}{self.model}{C.RESET}   {C.GRAY}接口：{C.RESET}{self.cfg.get('base_url')}
  {C.GRAY}API Key：{C.RESET}{key_state}   {C.GRAY}流式：{C.RESET}{'开' if self.stream else '关'}   {C.GRAY}思考过程：{C.RESET}{'显示' if self.show_reasoning else '隐藏'}
  {C.GRAY}工具：{C.RESET}{tool_state}
  {C.GRAY}输入内容开始对话，/help 查看命令，/exit 退出{C.RESET}
""")

    def _stream_reply(self, user_text: str) -> None:
        """调用接口并实时打印回复。"""
        self.session.add_user(user_text)
        payload_msgs = self.session.api_messages()

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        usage: Dict[str, Any] = {}
        started_reasoning = False
        started_content = False
        finish_reason = ""

        print(f"{C.GRAY}{C.DIM}⏳ 请求中…{C.RESET}", end="", flush=True)
        try:
            for event in self._run_events(payload_msgs):
                etype = event["type"]

                if etype == "retry":
                    print(
                        f"\r{C.YELLOW}⚠ 第 {event['attempt']} 次重试（{event['delay']:.1f}s 后）…{C.RESET}".ljust(60),
                        end="",
                        flush=True,
                    )
                elif etype == "reasoning":
                    if not started_reasoning:
                        started_reasoning = True
                        if self.show_reasoning:
                            print(f"\r{C.GRAY}{C.ITALIC}🧠 思考中…{C.RESET}")
                            print(f"{C.GRAY}{C.ITALIC}", end="", flush=True)
                        else:
                            print(f"\r{C.GRAY}⏳ 思考中…{C.RESET}".ljust(40), end="", flush=True)
                    reasoning_parts.append(event["text"])
                    if self.show_reasoning:
                        print(event["text"], end="", flush=True)
                elif etype == "content":
                    if not started_content:
                        started_content = True
                        if started_reasoning and self.show_reasoning:
                            print(f"{C.RESET}\n")
                        else:
                            print("\r" + " " * 40 + "\r", end="")
                        print(f"{C.CYAN}{C.BOLD}MiMo ›{C.RESET} ", end="", flush=True)
                    content_parts.append(event["text"])
                    print(event["text"], end="", flush=True)
                elif etype == "usage":
                    usage = event["usage"]
                elif etype == "round":
                    if started_content or started_reasoning:
                        print()
                    print(f"{C.GRAY}── 第 {event['index']} / {event['max']} 轮 ──{C.RESET}")
                    started_content = False
                    started_reasoning = False
                elif etype == "notice":
                    print(f"{C.YELLOW}⚠ {event.get('message', '')}{C.RESET}")
                elif etype == "tool_call":
                    if started_content:
                        print()
                    started_content = False
                    args_text = json.dumps(event.get("arguments") or {}, ensure_ascii=False)
                    if len(args_text) > 120:
                        args_text = args_text[:120] + "…"
                    print(f"{C.MAGENTA}⚙ 调用工具{C.RESET} {C.BOLD}{event['name']}{C.RESET} {C.GRAY}{args_text}{C.RESET}")
                elif etype == "tool_result":
                    mark = f"{C.GREEN}✓{C.RESET}" if event.get("ok") else f"{C.RED}✗{C.RESET}"
                    preview = str(event.get("content") or "").replace("\n", " ")
                    if len(preview) > 110:
                        preview = preview[:110] + "…"
                    print(f"  {mark} {C.GRAY}{event['name']} 返回（{event.get('elapsed', 0):.2f}s）：{preview}{C.RESET}")
                elif etype == "done":
                    usage = event.get("usage") or usage
                    finish_reason = event.get("finish_reason") or ""
        except KeyboardInterrupt:
            print(f"\n{C.YELLOW}⏸ 已中断本次生成。{C.RESET}")
        except MiMoError as exc:
            print(f"\n{C.RED}✗ 调用失败：{exc}{C.RESET}")
            # 撤回这条 user 消息，避免污染上下文；内容暂存以便 /retry
            if self.session.messages and self.session.messages[-1]["role"] == "user":
                self.session.messages.pop()
            self.pending_user = user_text
            print(f"{C.GRAY}输入 /retry 可重试本次提问。{C.RESET}")
            return

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)

        if not content and not reasoning:
            print(f"\n{C.YELLOW}（没有收到内容，可能是网络中断或额度不足）{C.RESET}")
            return

        if not started_content:
            print("\r" + " " * 40 + "\r", end="")
            if content:
                print(f"{C.CYAN}{C.BOLD}MiMo ›{C.RESET} {content}")
            elif reasoning:
                print(f"{C.YELLOW}（本轮只有思考内容，没有正文输出）{C.RESET}")
        else:
            print()

        self.last_answer = content
        self.last_reasoning = reasoning
        self.session.add_assistant(content, reasoning)
        self.session.turns += 1

        if usage:
            self.session.accumulate_usage(usage)
            pt = usage.get("prompt_tokens", "-")
            ct = usage.get("completion_tokens", "-")
            tt = usage.get("total_tokens", "-")
            tail = f"   {C.GRAY}⚑ {finish_reason}{C.RESET}" if finish_reason and finish_reason != "stop" else ""
            print(f"{C.GRAY}{C.DIM}   ── tokens: 输入 {pt} / 输出 {ct} / 合计 {tt}{C.RESET}{tail}")

    # ------------------------------------------------------------ 命令 --

    def handle_command(self, line: str) -> bool:
        """处理以 / 开头的命令。返回 False 表示要退出程序。"""
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower() if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("exit", "quit", "q"):
            return False

        if cmd == "help":
            print(HELP_TEXT)

        elif cmd == "new":
            self.session.reset()
            print(f"{C.GREEN}✓ 已开启新对话（上下文已清空）。{C.RESET}")

        elif cmd == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            self._print_banner()

        elif cmd == "models":
            print(f"{C.BOLD}内置模型清单：{C.RESET}")
            for item in KNOWN_MODELS:
                mark = f"{C.GREEN}●{C.RESET}" if item["id"] == self.model else " "
                print(f"  {mark} {C.BOLD}{item['id']}{C.RESET}  {C.GRAY}{item['desc']}{C.RESET}")
            try:
                remote = self.client.list_models()
                if remote:
                    print(f"\n{C.BOLD}接口返回的模型：{C.RESET}{', '.join(remote)}")
            except MiMoError:
                print(f"{C.GRAY}（该接口未提供模型列表，以上为内置清单）{C.RESET}")

        elif cmd == "model":
            if not arg:
                print(f"当前模型：{C.BOLD}{self.model}{C.RESET}（用 /models 查看可选项）")
            else:
                self.cfg["model"] = arg
                self._client = None
                self._runner = None
                save_config(self.cfg, self.config_path)
                print(f"{C.GREEN}✓ 已切换模型为 {C.BOLD}{arg}{C.RESET}")

        elif cmd == "system":
            if not arg:
                cur = self.session.system_prompt or "(空)"
                print(f"{C.BOLD}当前系统提示词：{C.RESET}\n{C.GRAY}{cur}{C.RESET}")
            else:
                self.base_system_prompt = arg
                self.session.system_prompt = self._compose_system_prompt()
                self.cfg["system_prompt"] = arg
                save_config(self.cfg, self.config_path)
                print(f"{C.GREEN}✓ 系统提示词已更新（对后续对话生效）。{C.RESET}")

        elif cmd == "tools":
            if arg in ("on", "off"):
                want = arg == "on"
                if want and (self.tool_ctx is None or not self.tool_ctx.enabled):
                    tools_cfg = dict(self.cfg.get("tools") or {})
                    tools_cfg["enabled"] = True
                    self.tool_ctx = build_tools({**self.cfg, "tools": tools_cfg}, APP_DIR)
                    self._runner = None
                if self.tool_ctx is None or self.tool_ctx.tool_count == 0:
                    print(f"{C.YELLOW}没有可用工具，请检查 config.json 的 tools 配置。{C.RESET}")
                else:
                    self.tool_ctx.enabled = want
                    self.cfg.setdefault("tools", {})["enabled"] = want
                    self._runner = None
                    self.session.system_prompt = self._compose_system_prompt()
                    save_config(self.cfg, self.config_path)
                    print(f"{C.GREEN}✓ 工具调用已{'开启' if want else '关闭'}（当前 {self.tool_ctx.tool_count} 个工具）。{C.RESET}")
            else:
                self._print_tools()

        elif cmd == "tool":
            self._invoke_tool(arg)

        elif cmd == "set":
            if "=" not in arg:
                print(f"{C.YELLOW}格式：/set 键=值，例：/set temperature=0.6{C.RESET}")
            else:
                key, _, raw = arg.partition("=")
                key, raw = key.strip(), raw.strip()
                value = self._coerce(raw)
                if key in ("temperature", "top_p", "max_completion_tokens", "max_tokens",
                           "frequency_penalty", "presence_penalty", "stop"):
                    self.params[key] = value
                    self.cfg.setdefault("params", {})[key] = value
                else:
                    self.extra_body[key] = value
                    self.cfg.setdefault("extra_body", {})[key] = value
                save_config(self.cfg, self.config_path)
                print(f"{C.GREEN}✓ 已设置 {key} = {json.dumps(value, ensure_ascii=False)}{C.RESET}")

        elif cmd == "unset":
            removed = False
            for store in (self.params, self.extra_body):
                if arg in store:
                    store.pop(arg)
                    removed = True
            for key in ("params", "extra_body"):
                self.cfg.get(key, {}).pop(arg, None)
            save_config(self.cfg, self.config_path)
            print(f"{C.GREEN}✓ 已移除 {arg}{C.RESET}" if removed else f"{C.YELLOW}未找到参数 {arg}{C.RESET}")

        elif cmd == "params":
            print(f"{C.BOLD}采样参数：{C.RESET}{json.dumps(self.params, ensure_ascii=False)}")
            print(f"{C.BOLD}额外参数：{C.RESET}{json.dumps(self.extra_body, ensure_ascii=False) or '{}'}")
            print(f"{C.GRAY}提示：思考模式下 temperature / top_p 会被模型强制为默认值 1.0 / 0.95。{C.RESET}")

        elif cmd == "stream":
            if arg in ("on", "off"):
                self.stream = arg == "on"
                self.cfg["stream"] = self.stream
                save_config(self.cfg, self.config_path)
                print(f"{C.GREEN}✓ 流式输出已{'开启' if self.stream else '关闭'}。{C.RESET}")
            else:
                print(f"当前：{'开' if self.stream else '关'}（用法：/stream on|off）")

        elif cmd == "think":
            if arg in ("on", "off"):
                self.show_reasoning = arg == "on"
                self.cfg["show_reasoning"] = self.show_reasoning
                save_config(self.cfg, self.config_path)
                print(f"{C.GREEN}✓ 思考过程显示已{'开启' if self.show_reasoning else '关闭'}。{C.RESET}")
            else:
                print(f"当前：{'显示' if self.show_reasoning else '隐藏'}（用法：/think on|off）")

        elif cmd == "history":
            if not self.session.messages:
                print(f"{C.GRAY}（当前上下文为空）{C.RESET}")
            else:
                print(f"{C.BOLD}共 {self.session.turns} 轮对话：{C.RESET}")
                for i, msg in enumerate(self.session.messages, 1):
                    role = msg["role"]
                    text = str(msg.get("content") or "").replace("\n", " ")
                    color = C.BLUE if role == "user" else C.CYAN
                    if len(text) > 100:
                        text = text[:100] + "…"
                    print(f"  {C.GRAY}{i:>2}.{C.RESET} {color}{role:<9}{C.RESET} {text}")

        elif cmd == "undo":
            removed = self.session.undo_last_turn()
            print(f"{C.GREEN}✓ 已撤销上一轮（删除 {removed} 条消息）。{C.RESET}" if removed
                  else f"{C.YELLOW}没有可撤销的内容。{C.RESET}")

        elif cmd == "retry":
            target = self.pending_user
            if not target and self.session.messages and self.session.messages[-1]["role"] == "user":
                target = str(self.session.messages[-1].get("content") or "")
            if not target:
                print(f"{C.YELLOW}没有待重试的提问。如需重新生成上一轮回答，请先 /undo 再 /retry。{C.RESET}")
            else:
                self.pending_user = ""
                print(f"{C.GRAY}↻ 重新生成…{C.RESET}")
                self._stream_reply(target)

        elif cmd == "save":
            name = arg or _dt.datetime.now().strftime("chat-%Y%m%d-%H%M%S")
            path = save_session(self.session, name)
            print(f"{C.GREEN}✓ 已保存到 {path}{C.RESET}")

        elif cmd == "load":
            files = list_sessions()
            if not arg:
                if not files:
                    print(f"{C.YELLOW}暂无已保存的对话。{C.RESET}")
                else:
                    print(f"{C.BOLD}已保存的对话：{C.RESET}")
                    for f in files[:20]:
                        print(f"  {f}")
                    print(f"{C.GRAY}用法：/load <文件名>{C.RESET}")
            else:
                try:
                    self.session = load_session(arg)
                    print(f"{C.GREEN}✓ 已载入 {arg}（{self.session.turns} 轮对话）。{C.RESET}")
                except FileNotFoundError:
                    print(f"{C.RED}✗ 找不到 sessions/{arg}{C.RESET}")
                except Exception as exc:  # noqa: BLE001
                    print(f"{C.RED}✗ 载入失败：{exc}{C.RESET}")

        elif cmd == "sessions":
            files = list_sessions()
            if not files:
                print(f"{C.GRAY}暂无已保存的对话。{C.RESET}")
            else:
                for f in files:
                    full = os.path.join(SESSION_DIR, f)
                    ts = _dt.datetime.fromtimestamp(os.path.getmtime(full)).strftime("%Y-%m-%d %H:%M")
                    size = os.path.getsize(full)
                    print(f"  {C.BOLD}{f}{C.RESET}  {C.GRAY}{ts}  {size / 1024:.1f} KB{C.RESET}")

        elif cmd == "usage":
            u = self.session.usage_total
            print(
                f"{C.BOLD}本会话累计用量：{C.RESET}输入 {u['prompt_tokens']} / "
                f"输出 {u['completion_tokens']} / 合计 {u['total_tokens']} tokens"
                f"（{self.session.turns} 轮对话）"
            )

        elif cmd == "config":
            shown = json.loads(json.dumps(self.cfg))
            shown["api_key"] = mask_key(self.cfg.get("api_key") or "")
            print(f"{C.BOLD}当前配置（{self.config_path}）：{C.RESET}")
            print(json.dumps(shown, ensure_ascii=False, indent=2))

        elif cmd == "":
            print(f"{C.YELLOW}请输入命令名，例如 /help{C.RESET}")

        else:
            print(f"{C.YELLOW}未知命令 /{cmd}，输入 /help 查看全部命令。{C.RESET}")

        return True

    @staticmethod
    def _coerce(raw: str) -> Any:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # ------------------------------------------------------------ 主循环 --

    def run(self) -> None:
        self._print_banner()
        if not (self.cfg.get("api_key") or os.environ.get("MIMO_API_KEY")):
            print(f"{C.RED}✗ 还没有配置 API Key。{C.RESET}")
            print(f"  {C.GRAY}方式一：set MIMO_API_KEY=sk-xxxxx   （Windows CMD）{C.RESET}")
            print(f"  {C.GRAY}方式二：export MIMO_API_KEY=sk-xxxxx（Git Bash / macOS / Linux）{C.RESET}")
            print(f"  {C.GRAY}方式三：在 config.json 里填写 \"api_key\": \"sk-xxxxx\"{C.RESET}\n")

        while True:
            try:
                line = self._read_input()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{C.GRAY}再见 👋{C.RESET}")
                break

            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    self._auto_save()
                    print(f"{C.GRAY}再见 👋{C.RESET}")
                    break
                continue
            self._stream_reply(line)

    def _read_input(self) -> str:
        prompt = f"{C.GREEN}你{C.RESET} {C.GRAY}›{C.RESET} "
        first = input(prompt)
        if not first.rstrip().endswith("\\"):
            return first
        lines = [first.rstrip()[:-1]]
        while True:
            cont = input(f"{C.GRAY}… {C.RESET}")
            if cont.rstrip().endswith("\\"):
                lines.append(cont.rstrip()[:-1])
            else:
                lines.append(cont)
                break
        return "\n".join(lines)

    def _auto_save(self) -> None:
        """退出时若对话非空，自动存档一份。"""
        if not self.session.messages:
            return
        try:
            path = save_session(self.session, _dt.datetime.now().strftime("auto-%Y%m%d-%H%M%S"))
            print(f"{C.GRAY}已自动保存本次对话：{path}{C.RESET}")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="assistant.py",
        description="本地 MiMo 对话助手（命令行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python assistant.py                       交互式对话
  python assistant.py -p "用一句话解释量子纠缠"
  python assistant.py --model mimo-v2.6-flash --no-stream
  python assistant.py --check               自检 API Key 是否可用
""",
    )
    parser.add_argument("-p", "--prompt", help="单次提问后直接输出结果并退出")
    parser.add_argument("-m", "--model", help="指定模型，如 mimo-v2.6-pro")
    parser.add_argument("--api-key", help="临时指定 API Key（不写入配置文件）")
    parser.add_argument("--base-url", help="指定接口地址")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--system", help="临时指定系统提示词")
    parser.add_argument("--temperature", type=float, help="采样温度")
    parser.add_argument("--max-tokens", type=int, help="最大输出 token 数")
    parser.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    parser.add_argument("--tools", action="store_true", help="本次启动强制开启工具调用（内置 / 技能 / MCP）")
    parser.add_argument("--no-tools", action="store_true", help="本次启动强制关闭工具调用")
    parser.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    parser.add_argument("--check", action="store_true", help="连通性自检后退出")
    parser.add_argument("--version", action="version", version=f"MiMo Assistant {VERSION}")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    use_utf8_stdout()
    args = parse_args(argv)

    if args.no_color or os.environ.get("NO_COLOR"):
        disable_color()
    else:
        enable_ansi()

    cfg = load_config(args.config)
    if args.api_key:
        cfg["api_key"] = args.api_key
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("MIMO_API_KEY", "")
    if args.model:
        cfg["model"] = args.model
    if args.base_url:
        cfg["base_url"] = args.base_url
    if args.system:
        cfg["system_prompt"] = args.system
    if args.temperature is not None:
        cfg.setdefault("params", {})["temperature"] = args.temperature
    if args.max_tokens is not None:
        cfg.setdefault("params", {})["max_completion_tokens"] = args.max_tokens
    if args.no_stream:
        cfg["stream"] = False
    if args.tools:
        cfg.setdefault("tools", {})["enabled"] = True
    if args.no_tools:
        cfg.setdefault("tools", {})["enabled"] = False

    assistant = Assistant(cfg, args.config)

    # ---- 自检 ----
    if args.check:
        key = cfg.get("api_key") or ""
        print(f"接口地址 : {cfg.get('base_url')}")
        print(f"模型     : {cfg.get('model')}")
        print(f"API Key  : {mask_key(key)}")
        if assistant.tool_ctx and assistant.tool_ctx.enabled:
            print(f"工具     : {assistant.tool_ctx.tool_count} 个"
                  + (f"（{len(assistant.tool_ctx.errors)} 个来源连接失败）" if assistant.tool_ctx.errors else ""))
        else:
            print("工具     : 未开启")
        if not key:
            print(f"{C.RED}✗ 未配置 API Key{C.RESET}")
            assistant.close()
            return 2
        t0 = time.time()
        try:
            reply = assistant.client.ping()
            print(f"{C.GREEN}✓ 连接成功（{time.time() - t0:.2f}s）{C.RESET}")
            print(f"  模型回复：{reply}")
            assistant.close()
            return 0
        except MiMoError as exc:
            print(f"{C.RED}✗ 连接失败：{exc}{C.RESET}")
            return 1

    # ---- 单次问答 ----
    if args.prompt:
        assistant._stream_reply(args.prompt)
        assistant.close()
        return 0

    # ---- 交互式 ----
    try:
        assistant.run()
    finally:
        assistant.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
