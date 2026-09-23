# -*- coding: utf-8 -*-
"""
server.py —— MiMo 助手的本地网页版服务

* 零第三方依赖：仅用 Python 标准库
* 静态托管 web/ 目录下的界面
* /api/chat 把浏览器请求代理到 MiMo，并以 SSE 流式回推（支持思考过程）
* /api/config 读写 config.json，/api/test 做连通性自检
* 默认只监听 127.0.0.1，不对外网开放

启动：
    python server.py                 # 默认 http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-browser    # 不自动打开浏览器
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from assistant import (  # noqa: E402
    CONFIG_PATH,
    build_system_prompt,
    load_config,
    mask_key,
    save_config,
)
from mimo_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    KNOWN_MODELS,
    MiMoClient,
    MiMoError,
)
from mimo_tools import AgentRunner, ToolContext, build_tools  # noqa: E402

WEB_DIR = os.path.join(APP_DIR, "web")
VERSION = "1.1.0"

# 允许通过网页写入配置的字段白名单
WRITABLE_FIELDS = (
    "base_url",
    "model",
    "system_prompt",
    "stream",
    "show_reasoning",
    "timeout",
    "max_retries",
    "proxy",
    "auth_scheme",
)
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


def public_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """去掉敏感字段后的配置，用于回传前端。"""
    key = cfg.get("api_key") or os.environ.get("MIMO_API_KEY", "")
    return {
        "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
        "model": cfg.get("model") or DEFAULT_MODEL,
        "system_prompt": cfg.get("system_prompt") or "",
        "stream": bool(cfg.get("stream", True)),
        "show_reasoning": bool(cfg.get("show_reasoning", True)),
        "timeout": cfg.get("timeout", 180),
        "max_retries": cfg.get("max_retries", 3),
        "proxy": cfg.get("proxy") or "",
        "auth_scheme": cfg.get("auth_scheme") or "both",
        "params": cfg.get("params") or {},
        "extra_body": cfg.get("extra_body") or {},
        "has_key": bool(key),
        "key_masked": mask_key(key),
        "models": KNOWN_MODELS,
        "version": VERSION,
        "tools": (cfg.get("tools") or {}),
    }


# --------------------------------------------------------------------------- #
# 工具扩展层的全局状态（MCP 子进程很贵，不能每个请求都重建）
# --------------------------------------------------------------------------- #

_TOOL_LOCK = threading.Lock()
_TOOL_CTX: Optional[ToolContext] = None
_TOOL_SIGNATURE = ""


def tools_signature(cfg: Dict[str, Any]) -> str:
    try:
        return json.dumps(cfg.get("tools") or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def get_tool_context(cfg: Dict[str, Any], force: bool = False) -> ToolContext:
    """按当前配置取工具上下文；配置没变就复用已有连接。"""
    global _TOOL_CTX, _TOOL_SIGNATURE
    signature = tools_signature(cfg)
    with _TOOL_LOCK:
        if force or _TOOL_CTX is None or signature != _TOOL_SIGNATURE:
            if _TOOL_CTX is not None:
                _TOOL_CTX.close()
            _TOOL_CTX = build_tools(cfg, APP_DIR)
            _TOOL_SIGNATURE = signature
        return _TOOL_CTX


def close_tool_context() -> None:
    global _TOOL_CTX, _TOOL_SIGNATURE
    with _TOOL_LOCK:
        if _TOOL_CTX is not None:
            _TOOL_CTX.close()
        _TOOL_CTX = None
        _TOOL_SIGNATURE = ""


def make_client(cfg: Dict[str, Any], model: Optional[str] = None) -> MiMoClient:
    return MiMoClient(
        api_key=cfg.get("api_key") or os.environ.get("MIMO_API_KEY", ""),
        base_url=cfg.get("base_url") or DEFAULT_BASE_URL,
        model=model or cfg.get("model") or DEFAULT_MODEL,
        timeout=cfg.get("timeout", 180),
        max_retries=cfg.get("max_retries", 3),
        auth_scheme=cfg.get("auth_scheme") or "both",
        proxy=cfg.get("proxy") or None,
    )


class Handler(BaseHTTPRequestHandler):
    server_version = f"MiMoWeb/{VERSION}"
    protocol_version = "HTTP/1.1"
    config_path = CONFIG_PATH

    # ------------------------------------------------------------ 工具方法 --

    def log_message(self, fmt: str, *args: Any) -> None:
        # 只打印 API 请求，静态资源不刷屏
        if "/api/" in (self.path or ""):
            sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _sse(self, event: Dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        self.wfile.write(b"data: " + payload + b"\n\n")
        self.wfile.flush()

    # ------------------------------------------------------------ 路由 --

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/config":
            self._send_json(public_config(load_config(self.config_path)))
            return

        if path == "/api/models":
            cfg = load_config(self.config_path)
            try:
                remote = make_client(cfg).list_models()
                self._send_json({"ok": True, "models": remote})
            except MiMoError as exc:
                self._send_json({"ok": False, "models": [], "message": str(exc)})
            return

        if path == "/api/tools":
            cfg = load_config(self.config_path)
            ctx = get_tool_context(cfg)
            self._send_json({"ok": True, **ctx.describe()})
            return

        if path == "/api/health":
            self._send_json({"ok": True, "version": VERSION})
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._serve_static("/index.html" if path in ("/", "") else path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/chat":
            self._handle_chat()
        elif path == "/api/config":
            self._handle_config_save()
        elif path == "/api/test":
            self._handle_test()
        elif path == "/api/tools/reload":
            cfg = load_config(self.config_path)
            ctx = get_tool_context(cfg, force=True)
            self._send_json({"ok": True, **ctx.describe()})
        elif path == "/api/tool/call":
            self._handle_tool_call()
        else:
            self._send_json({"error": "未知接口"}, status=404)

    # ------------------------------------------------------------ 静态资源 --

    def _serve_static(self, path: str) -> None:
        rel = unquote(path).lstrip("/")
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR) or not os.path.isfile(target):
            self._send_json({"error": "文件不存在", "path": path}, status=404)
            return
        ext = os.path.splitext(target)[1].lower()
        ctype = STATIC_TYPES.get(ext) or mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fp:
            body = fp.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ 配置读写 --

    def _handle_config_save(self) -> None:
        incoming = self._read_json()
        cfg = load_config(self.config_path)

        api_key = incoming.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            cfg["api_key"] = api_key.strip()
        elif incoming.get("clear_api_key") is True:
            cfg["api_key"] = ""

        for field in WRITABLE_FIELDS:
            if field in incoming:
                cfg[field] = incoming[field]

        if isinstance(incoming.get("params"), dict):
            params = dict(cfg.get("params") or {})
            for key, value in incoming["params"].items():
                if value is None:
                    params.pop(key, None)
                else:
                    params[key] = value
            cfg["params"] = params

        if isinstance(incoming.get("extra_body"), dict):
            cfg["extra_body"] = incoming["extra_body"]

        # 工具扩展配置：只允许改开关与轮次上限，其余细节留在 config.json 里手工维护
        if isinstance(incoming.get("tools"), dict):
            tools_cfg = dict(cfg.get("tools") or {})
            for key in ("enabled", "max_iterations", "sandbox_root"):
                if key in incoming["tools"]:
                    tools_cfg[key] = incoming["tools"][key]
            for key in ("builtin", "skills", "mcp"):
                if isinstance(incoming["tools"].get(key), dict):
                    merged = dict(tools_cfg.get(key) or {})
                    merged.update(incoming["tools"][key])
                    tools_cfg[key] = merged
            cfg["tools"] = tools_cfg

        try:
            save_config(cfg, self.config_path)
        except OSError as exc:
            self._send_json({"ok": False, "message": f"写入配置失败：{exc}"}, status=500)
            return
        # 工具配置可能变了：下一次取用时会自动重建连接
        self._send_json({"ok": True, "config": public_config(cfg)})

    def _handle_test(self) -> None:
        cfg = load_config(self.config_path)
        body = self._read_json()
        model = body.get("model") or cfg.get("model")
        started = time.time()
        try:
            reply = make_client(cfg, model=model).ping()
            self._send_json(
                {
                    "ok": True,
                    "latency": round(time.time() - started, 2),
                    "reply": reply,
                    "model": model,
                }
            )
        except MiMoError as exc:
            self._send_json({"ok": False, "message": str(exc), "model": model})

    def _handle_tool_call(self) -> None:
        """手动执行一个工具，方便调试技能 / MCP。"""
        body = self._read_json()
        name = str(body.get("name") or "").strip()
        if not name:
            self._send_json({"ok": False, "message": "缺少工具名"}, status=400)
            return
        ctx = get_tool_context(load_config(self.config_path))
        if not ctx.enabled or ctx.tool_count == 0:
            self._send_json({"ok": False, "message": "工具扩展层未开启"}, status=400)
            return
        result = ctx.registry.call(name, body.get("arguments") or {})
        self._send_json(
            {
                "ok": result.ok,
                "name": name,
                "content": result.content,
                "elapsed": round(result.elapsed, 3),
            }
        )

    # ------------------------------------------------------------ 对话流 --

    def _handle_chat(self) -> None:
        body = self._read_json()
        cfg = load_config(self.config_path)

        raw_messages = body.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            self._send_json({"error": "messages 不能为空"}, status=400)
            return

        messages = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in ("system", "user", "assistant"):
                continue
            msg: Dict[str, Any] = {"role": role, "content": item.get("content") or ""}
            if role == "assistant" and item.get("reasoning_content"):
                msg["reasoning_content"] = item["reasoning_content"]
            messages.append(msg)

        if not messages:
            self._send_json({"error": "messages 格式不正确"}, status=400)
            return

        model = body.get("model") or cfg.get("model") or DEFAULT_MODEL
        params = dict(cfg.get("params") or {})
        if isinstance(body.get("params"), dict):
            params.update(body["params"])
        extra_body = dict(cfg.get("extra_body") or {})
        if isinstance(body.get("extra_body"), dict):
            extra_body.update(body["extra_body"])
        stream = bool(body.get("stream", cfg.get("stream", True)))

        # 强制把系统提示词放在最前面（含提示词技能与 MCP 使用说明）
        tool_ctx = get_tool_context(cfg)
        use_tools = bool(body.get("tools", True)) and tool_ctx.enabled and tool_ctx.tool_count > 0
        if not messages or messages[0].get("role") != "system":
            system_prompt = build_system_prompt(cfg.get("system_prompt") or "")
            if use_tools:
                system_prompt += tool_ctx.system_prompt_suffix()
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})

        try:
            client = make_client(cfg, model=model)
        except MiMoError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        try:
            self._sse({"type": "start", "model": model, "tools": tool_ctx.tool_count if use_tools else 0})
            if use_tools:
                runner = AgentRunner(client, tool_ctx.registry, tool_ctx.max_iterations)
                events = runner.run(messages, params=params, extra_body=extra_body, stream=stream)
            else:
                events = client.chat(messages, stream=stream, params=params, extra_body=extra_body)
            for event in events:
                self._sse(event)
        except MiMoError as exc:
            try:
                self._sse({"type": "error", "message": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass  # 用户点了「停止」或关掉页面
        except Exception as exc:  # noqa: BLE001
            try:
                self._sse({"type": "error", "message": f"服务异常：{exc}"})
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            try:
                self._sse({"type": "end"})
            except Exception:
                pass


def main(argv: Optional[list] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="server.py", description="MiMo 助手的本地网页版服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--config", default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    if not os.path.isdir(WEB_DIR):
        print(f"✗ 找不到界面目录：{WEB_DIR}")
        return 1

    Handler.config_path = args.config
    cfg = load_config(args.config)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"

    # 启动时就把工具装配一遍，MCP 连不上之类的问题立刻暴露出来
    tool_ctx = get_tool_context(cfg)
    if tool_ctx.enabled:
        sources: Dict[str, int] = {}
        for item in tool_ctx.registry.describe():
            sources[item["source"]] = sources.get(item["source"], 0) + 1
        tool_line = f"{tool_ctx.tool_count} 个（" + "、".join(f"{k}×{v}" for k, v in sorted(sources.items())) + "）"
        if tool_ctx.prompts:
            tool_line += f" + {len(tool_ctx.prompts)} 段提示词"
    else:
        tool_line = "未开启"

    print("")
    print("  ╭────────────────────────────────────────────╮")
    print(f"  │        MiMo 助手 · 网页版  v{VERSION}           │")
    print("  ╰────────────────────────────────────────────╯")
    print(f"  地址   : {url}")
    print(f"  模型   : {cfg.get('model') or DEFAULT_MODEL}")
    print(f"  接口   : {cfg.get('base_url') or DEFAULT_BASE_URL}")
    print(f"  密钥   : {mask_key(cfg.get('api_key') or os.environ.get('MIMO_API_KEY', ''))}")
    print(f"  工具   : {tool_line}")
    for note in tool_ctx.notes:
        print(f"           · {note}")
    for err in tool_ctx.errors:
        print(f"           ! {err}")
    print("  停止   : 按 Ctrl+C")
    print("")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止服务，再见 👋")
    finally:
        httpd.server_close()
        close_tool_context()
    return 0


if __name__ == "__main__":
    sys.exit(main())
