# -*- coding: utf-8 -*-
"""
mimo_tools.mcp —— MCP（Model Context Protocol）客户端

只实现助手真正需要的那一小部分协议，零第三方依赖：

    initialize  →  notifications/initialized  →  tools/list  →  tools/call

支持两种传输：

  * stdio            本地子进程，用换行分隔的 JSON-RPC 消息通信（最常用）
  * http             Streamable HTTP，POST JSON-RPC，响应可能是 JSON 也可能是 SSE

配置文件 mcp.json：

    {
      "servers": {
        "filesystem": {
          "enabled": true,
          "transport": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "Y:/A_MiMo"],
          "env": {},
          "cwd": ""
        },
        "remote": {
          "enabled": false,
          "transport": "http",
          "url": "http://127.0.0.1:3000/mcp",
          "headers": {"Authorization": "Bearer xxx"}
        }
      }
    }

MCP 工具会被桥接成普通 Tool 注册进 ToolRegistry，模型侧看到的仍是标准 tools 规范。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Tool, ToolError, ToolRegistry, sanitize_tool_name

__all__ = ["McpError", "McpServerSpec", "McpManager", "load_mcp_specs"]

DEFAULT_TIMEOUT = 30.0
PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "mimo-local-assistant", "version": "1.0.0"}


class McpError(Exception):
    """MCP 通信或协议错误。"""


@dataclass
class McpServerSpec:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    enabled: bool = True
    description: str = ""


def load_mcp_specs(path: str) -> Dict[str, McpServerSpec]:
    """读取 mcp.json，返回 {名称: spec}。文件不存在时返回空字典。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        raise McpError(f"读取 {path} 失败：{exc}") from None

    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}

    specs: Dict[str, McpServerSpec] = {}
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            continue
        transport = str(raw.get("transport") or ("http" if raw.get("url") else "stdio")).lower()
        specs[name] = McpServerSpec(
            name=name,
            transport=transport,
            command=str(raw.get("command") or ""),
            args=[str(a) for a in (raw.get("args") or [])],
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            cwd=str(raw.get("cwd") or ""),
            url=str(raw.get("url") or ""),
            headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
            timeout=float(raw.get("timeout") or DEFAULT_TIMEOUT),
            enabled=bool(raw.get("enabled", True)),
            description=str(raw.get("description") or ""),
        )
    return specs


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #

class _StdioTransport:
    def __init__(self, spec: McpServerSpec):
        self.spec = spec
        self.proc: Optional[subprocess.Popen] = None
        self._cv = threading.Condition()
        self._responses: Dict[Any, Dict[str, Any]] = {}
        self._stderr: "queue.Queue[str]" = queue.Queue(maxsize=200)
        self._closed = False

    def start(self) -> None:
        if self.proc is not None:
            return
        command = self.spec.command
        if not command:
            raise McpError(f"MCP Server「{self.spec.name}」没有配置 command")

        resolved = shutil.which(command) or command
        argv = [resolved] + list(self.spec.args)

        env = os.environ.copy()
        env.update(self.spec.env)
        env.setdefault("PYTHONIOENCODING", "utf-8")

        kwargs: Dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        cwd = self.spec.cwd or None
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd,
                env=env,
                **kwargs,
            )
        except OSError as exc:
            raise McpError(f"启动 MCP Server「{self.spec.name}」失败：{exc}") from None

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            msg_id = message.get("id")
            if msg_id is None:
                continue  # 服务端通知，忽略
            with self._cv:
                self._responses[msg_id] = message
                self._cv.notify_all()

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            try:
                self._stderr.put_nowait(line.rstrip())
            except queue.Full:
                pass

    def stderr_tail(self, limit: int = 6) -> str:
        lines = list(self._stderr.queue)[-limit:]
        return " | ".join(lines)

    def write(self, message: Dict[str, Any]) -> None:
        if self._closed or self.proc is None or self.proc.stdin is None:
            raise McpError(f"MCP Server「{self.spec.name}」连接已关闭")
        if self.proc.poll() is not None:
            tail = self.stderr_tail()
            raise McpError(
                f"MCP Server「{self.spec.name}」进程已退出（code={self.proc.returncode}）"
                + (f"，stderr：{tail}" if tail else "")
            )
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"写入 MCP Server「{self.spec.name}」失败：{exc}") from None

    def wait(self, msg_id: Any, timeout: float) -> Dict[str, Any]:
        deadline = time.time() + timeout
        with self._cv:
            while msg_id not in self._responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    tail = self.stderr_tail()
                    raise McpError(
                        f"MCP Server「{self.spec.name}」响应超时（{timeout:g}s）"
                        + (f"，stderr：{tail}" if tail else "")
                    )
                self._cv.wait(remaining)
            return self._responses.pop(msg_id)

    def close(self) -> None:
        self._closed = True
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _HttpTransport:
    def __init__(self, spec: McpServerSpec):
        self.spec = spec
        self.session_id = ""

    def start(self) -> None:
        if not self.spec.url:
            raise McpError(f"MCP Server「{self.spec.name}」没有配置 url")

    def write(self, message: Dict[str, Any]) -> None:
        pass  # HTTP 是一问一答，不需要单独写

    def request(self, message: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.spec.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request = urllib.request.Request(self.spec.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.spec.timeout) as resp:
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                content_type = (resp.headers.get("Content-Type") or "").lower()
                status = resp.status
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise McpError(f"MCP Server「{self.spec.name}」返回 HTTP {exc.code} {detail}") from None
        except Exception as exc:  # noqa: BLE001
            raise McpError(f"请求 MCP Server「{self.spec.name}」失败：{exc}") from None

        # 通知类请求（没有 id）服务端通常回 202 + 空 body
        if not raw.strip():
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}

        if "text/event-stream" in content_type:
            wanted = message.get("id")
            fallback: Optional[Dict[str, Any]] = None
            for line in raw.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                if parsed.get("id") == wanted:
                    return parsed
                if fallback is None and "result" in parsed:
                    fallback = parsed
            if fallback is not None:
                return fallback
            raise McpError(f"MCP Server「{self.spec.name}」的 SSE 响应里没有匹配的 JSON-RPC 结果")

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            raise McpError(f"MCP Server「{self.spec.name}」返回了非 JSON 内容：{raw[:200]}") from None
        return result if isinstance(result, dict) else {"result": result}

    def close(self) -> None:
        if not self.session_id:
            return
        request = urllib.request.Request(
            self.spec.url,
            headers={"Mcp-Session-Id": self.session_id, **self.spec.headers},
            method="DELETE",
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

class McpClient:
    """一个 MCP Server 的连接。"""

    def __init__(self, spec: McpServerSpec):
        self.spec = spec
        self.transport: Any = _HttpTransport(spec) if spec.transport == "http" else _StdioTransport(spec)
        self._id = 0
        self._id_lock = threading.Lock()
        self.server_info: Dict[str, Any] = {}
        self.instructions: str = ""
        self.tools: List[Dict[str, Any]] = []

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    # ------------------------------------------------------------ 基础 --

    def connect(self) -> None:
        self.transport.start()
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.spec.timeout,
        )
        self.server_info = result.get("serverInfo") or {}
        self.instructions = result.get("instructions") or ""
        self.notify("notifications/initialized", {})

    def request(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        msg_id = self._next_id()
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        wait = float(timeout or self.spec.timeout)

        if isinstance(self.transport, _HttpTransport):
            response = self.transport.request(message)
        else:
            self.transport.write(message)
            response = self.transport.wait(msg_id, wait)

        if not isinstance(response, dict):
            raise McpError(f"MCP Server「{self.spec.name}」返回了非法响应")
        if response.get("error"):
            error = response["error"]
            text = error.get("message") if isinstance(error, dict) else str(error)
            raise McpError(f"MCP Server「{self.spec.name}」调用 {method} 出错：{text}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if isinstance(self.transport, _HttpTransport):
            try:
                self.transport.request(message)
            except McpError:
                pass  # 通知失败不影响主流程
            return
        try:
            self.transport.write(message)
        except McpError:
            pass

    # ------------------------------------------------------------ 工具 --

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = result.get("tools")
        self.tools = [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []
        return self.tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=max(self.spec.timeout, 60.0))
        if result.get("isError"):
            raise ToolError(self._flatten_content(result) or "工具执行失败")
        return self._flatten_content(result)

    @staticmethod
    def _flatten_content(result: Dict[str, Any]) -> str:
        parts = result.get("content")
        if not isinstance(parts, list):
            if result:
                return json.dumps(result, ensure_ascii=False)[:4000]
            return "（无返回内容）"
        chunks: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                chunks.append(str(part))
                continue
            kind = part.get("type")
            if kind == "text":
                chunks.append(str(part.get("text") or ""))
            elif kind == "resource":
                resource = part.get("resource") or {}
                chunks.append(str(resource.get("text") or resource.get("uri") or "（resource）"))
            else:
                chunks.append(f"（{kind or '未知'}类型内容，暂不支持展示）")
        return "\n".join(c for c in chunks if c) or "（无返回内容）"

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 管理器
# --------------------------------------------------------------------------- #

class McpManager:
    """管理多个 MCP Server 的生命周期，并把它们的工具桥接进 ToolRegistry。"""

    def __init__(self) -> None:
        self.clients: Dict[str, McpClient] = {}
        self.errors: List[str] = []
        self.notes: List[str] = []

    def connect_all(self, specs: Dict[str, McpServerSpec], include: Optional[List[str]] = None, exclude: Optional[List[str]] = None) -> None:
        include_set = set(include or [])
        exclude_set = set(exclude or [])
        for name, spec in specs.items():
            if not spec.enabled:
                continue
            if include_set and name not in include_set:
                continue
            if name in exclude_set:
                continue
            try:
                client = McpClient(spec)
                client.connect()
                client.list_tools()
                self.clients[name] = client
                version = (client.server_info or {}).get("version") or ""
                self.notes.append(
                    f"MCP「{name}」已连接"
                    + (f"（{(client.server_info or {}).get('name') or ''} {version}）".replace("  ", " ") if client.server_info else "")
                    + f"，提供 {len(client.tools)} 个工具"
                )
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"MCP「{name}」连接失败：{exc}")

    def bridge(self, registry: ToolRegistry) -> int:
        """把所有 MCP 工具注册进注册表，返回注册数量。"""
        count = 0
        for server_name, client in self.clients.items():
            for raw in client.tools:
                name = raw.get("name")
                if not name:
                    continue
                description = raw.get("description") or f"{server_name} 提供的工具"
                tool = Tool(
                    name=name,
                    description=f"[MCP:{server_name}] {description}",
                    parameters=raw.get("inputSchema") or {"type": "object", "properties": {}},
                    handler=self._make_handler(client, name),
                    source="mcp",
                    origin=f"mcp://{server_name}",
                    meta={"server": server_name, "mcp_tool": name},
                )
                registry.register(tool, namespace=server_name)
                count += 1
        return count

    @staticmethod
    def _make_handler(client: McpClient, tool_name: str):
        def handler(**kwargs: Any) -> str:
            return client.call_tool(tool_name, kwargs)

        handler.__name__ = sanitize_tool_name(tool_name)
        return handler

    def prompt_blocks(self) -> List[Dict[str, str]]:
        """MCP Server 通过 instructions 提供的使用说明，可拼进系统提示词。"""
        blocks = []
        for server_name, client in self.clients.items():
            if client.instructions:
                blocks.append({"name": f"MCP:{server_name}", "body": client.instructions})
        return blocks

    def close_all(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()
