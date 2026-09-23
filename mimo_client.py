# -*- coding: utf-8 -*-
"""
mimo_client.py —— Xiaomi MiMo API 客户端

特点：
  * 纯 Python 标准库实现，无需 pip 安装任何第三方包
  * 兼容 OpenAI Chat Completions 协议：POST {base_url}/chat/completions
  * 支持流式（SSE）与非流式两种模式
  * 支持 reasoning_content（思考过程）解析
  * 内置指数退避重试、超时控制、代理支持
  * 请求头同时携带 api-key 与 Authorization: Bearer（官方两种方式均支持）

官方文档：https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call
"""

from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

__all__ = [
    "MiMoClient",
    "MiMoError",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "KNOWN_MODELS",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.6-pro"

# 官方推荐的中文系统提示词（{date} {week} 由客户端自动填充）
DEFAULT_SYSTEM_PROMPT = (
    "你是MiMo（中文名称也是MiMo），是小米公司研发的AI智能助手。\n"
    "今天的日期：{date} {week}，你的知识截止日期是2024年12月。"
)

# 内置模型清单（用于 /models 命令与交互式选择；实际可用模型以控制台为准）
KNOWN_MODELS: List[Dict[str, str]] = [
    {"id": "mimo-v2.6-pro", "desc": "V2.6 旗舰推理模型，综合能力最强"},
    {"id": "mimo-v2.6-pro-ultraspeed", "desc": "V2.6 旗舰极速版，低延迟高吞吐"},
    {"id": "mimo-v2.6-flash", "desc": "V2.6 轻量版，日常对话/高并发性价比高"},
    {"id": "mimo-v2.5-pro", "desc": "V2.5 旗舰（官方公告 2026-10-21 下线）"},
    {"id": "mimo-v2.5", "desc": "V2.5 通用（官方公告 2026-10-21 下线）"},
]

# 会被透传给接口的采样参数（None 值会被自动剔除）
_PASSTHROUGH_KEYS = (
    "temperature",
    "top_p",
    "max_completion_tokens",
    "max_tokens",
    "stop",
    "frequency_penalty",
    "presence_penalty",
    "tool_choice",
    "tools",
    "response_format",
)


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #

class MiMoError(Exception):
    """MiMo API 调用失败。"""

    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body

    def __str__(self) -> str:  # pragma: no cover
        return self.args[0] if self.args else "MiMoError"


def _friendly_http_error(status: int, body: str) -> str:
    """把接口返回的错误体整理成人能看懂的提示。"""
    msg = ""
    try:
        payload = json.loads(body)
        err = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("msg") or json.dumps(err, ensure_ascii=False))
        else:
            msg = str(err)
    except Exception:
        msg = (body or "").strip()[:500]

    hints = {
        400: "请求参数有误，请检查模型名 / 参数取值（如 temperature 范围 [0, 1.5]）",
        401: "API Key 无效或未授权：请检查 config.json 或环境变量 MIMO_API_KEY",
        402: "账户余额不足：请到 MiMo 控制台充值，或改用 Token Plan 订阅",
        403: "无权访问该模型：请确认账号已开通对应模型 / Token Plan 是否在有效期内",
        404: "接口地址不存在：请检查 base_url。按量付费为 https://api.xiaomimimo.com/v1，"
             "Token Plan 为 https://token-plan-cn.xiaomimimo.com/v1",
        429: "触发限流或额度不足：请稍后重试，或到控制台检查余额 / 限速配额",
        500: "服务端异常，稍后重试通常可恢复",
        502: "网关异常，稍后重试",
        503: "服务暂时不可用，稍后重试",
    }
    hint = hints.get(status)
    return f"HTTP {status}: {msg or '未知错误'}" + (f"\n  ↳ 提示：{hint}" if hint else "")


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #

class MiMoClient:
    """Xiaomi MiMo API 的轻量客户端（线程无关，可复用于多轮对话）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
        max_retries: int = 3,
        auth_scheme: str = "both",
        proxy: Optional[str] = None,
    ):
        if not api_key:
            raise MiMoError(
                "缺少 API Key。请设置环境变量 MIMO_API_KEY，或在 config.json 里填写 api_key。"
            )
        self.api_key = api_key.strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.auth_scheme = auth_scheme or "both"
        self.proxy = proxy

        self._opener = self._build_opener()

    # ---------------------------------------------------------------- 基础 --

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers: List[Any] = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler())  # 读取 http_proxy / https_proxy 环境变量
        return urllib.request.build_opener(*handlers)

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "MiMo-Local-Assistant/1.0 (+python-urllib)",
        }
        if self.auth_scheme in ("api-key", "both"):
            headers["api-key"] = self.api_key
        if self.auth_scheme in ("bearer", "both"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _open(self, payload: Dict[str, Any]):
        """发起一次请求并返回响应对象（流式模式下需由调用方负责关闭）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data, headers=self._headers(), method="POST")
        return self._opener.open(req, timeout=self.timeout)

    # ------------------------------------------------------------ 参数拼装 --

    def build_payload(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = True,
        params: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "stream": bool(stream)}
        if params:
            for key in _PASSTHROUGH_KEYS:
                value = params.get(key)
                if value is not None:
                    payload[key] = value
        if extra_body:
            payload.update(extra_body)
        return payload

    # ---------------------------------------------------------------- 对话 --

    def chat(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = True,
        params: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        统一入口，以事件流的方式产出结果。

        产出的事件类型：
          {"type": "reasoning", "text": str}           思考过程增量
          {"type": "content",   "text": str}           正文增量
          {"type": "tool_call_start", "name": str, ...} 模型开始请求某个工具
          {"type": "tool_calls", "tool_calls": list}   本轮完整的工具调用请求
          {"type": "usage",     "usage": dict}         token 用量
          {"type": "done",      "content": str, "reasoning": str, "tool_calls": list,
                                "usage": dict, "finish_reason": str, "model": str}
        """
        payload = self.build_payload(messages, stream=stream, params=params, extra_body=extra_body)
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            emitted = False
            try:
                resp = self._open(payload)
                try:
                    if stream:
                        for event in self._iter_sse(resp):
                            if event["type"] in ("content", "reasoning"):
                                emitted = True
                            yield event
                    else:
                        for event in self._parse_full(resp):
                            emitted = True
                            yield event
                    return
                finally:
                    resp.close()
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")
                except Exception:
                    pass
                err = MiMoError(_friendly_http_error(exc.code, body), status=exc.code, body=body)
                # 4xx 属于调用方问题，不重试；429 / 5xx 可重试
                if exc.code < 500 and exc.code != 429:
                    raise err from None
                last_error = err
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = MiMoError(f"网络请求失败：{reason}")
            except MiMoError as exc:
                last_error = exc

            if emitted:
                # 已经吐出过内容，重试会导致重复输出，直接抛出
                raise last_error  # type: ignore[misc]

            if attempt < self.max_retries:
                delay = min(2 ** attempt + random.random(), 20.0)
                yield {"type": "retry", "attempt": attempt + 1, "delay": delay, "error": str(last_error)}
                time.sleep(delay)

        raise last_error  # type: ignore[misc]

    # ---------------------------------------------------------- 事件解析 --

    def _iter_sse(self, resp) -> Iterator[Dict[str, Any]]:
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_slots: Dict[int, Dict[str, Any]] = {}
        usage: Dict[str, Any] = {}
        finish_reason = ""
        model = self.model

        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if isinstance(chunk, dict) and chunk.get("error"):
                err = chunk["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise MiMoError(f"接口返回错误：{msg}")

            if chunk.get("model"):
                model = chunk["model"]
            if chunk.get("usage"):
                usage = chunk["usage"]

            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                rc = delta.get("reasoning_content")
                if rc:
                    reasoning_parts.append(rc)
                    yield {"type": "reasoning", "text": rc}
                piece = delta.get("content")
                if piece:
                    content_parts.append(piece)
                    yield {"type": "content", "text": piece}
                for call in delta.get("tool_calls") or []:
                    slot = self._accumulate_tool_call(tool_slots, call)
                    if slot.get("_just_named"):
                        slot.pop("_just_named", None)
                        yield {
                            "type": "tool_call_start",
                            "index": slot["_index"],
                            "id": slot["id"],
                            "name": slot["function"]["name"],
                        }
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        if usage:
            yield {"type": "usage", "usage": usage}
        tool_calls = self._finalize_tool_calls(tool_slots)
        if tool_calls:
            yield {"type": "tool_calls", "tool_calls": tool_calls}
        yield {
            "type": "done",
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "tool_calls": tool_calls,
            "usage": usage,
            "finish_reason": finish_reason,
            "model": model,
        }

    @staticmethod
    def _accumulate_tool_call(slots: Dict[int, Dict[str, Any]], call: Dict[str, Any]) -> Dict[str, Any]:
        """把流式返回的 tool_calls 增量拼成完整结构。"""
        index = call.get("index")
        if index is None:
            index = len(slots)
        index = int(index)
        slot = slots.get(index)
        if slot is None:
            slot = {
                "_index": index,
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
            slots[index] = slot

        if call.get("id"):
            slot["id"] = call["id"]
        if call.get("type"):
            slot["type"] = call["type"]

        func = call.get("function") or {}
        name = func.get("name")
        if name and not slot["function"]["name"]:
            slot["function"]["name"] = name
            slot["_just_named"] = True
        args = func.get("arguments")
        if args:
            slot["function"]["arguments"] += args
        return slot

    @staticmethod
    def _finalize_tool_calls(slots: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for index in sorted(slots):
            slot = dict(slots[index])
            slot.pop("_index", None)
            slot.pop("_just_named", None)
            if not slot.get("id"):
                slot["id"] = f"call_{index}"
            if not slot["function"].get("arguments"):
                slot["function"]["arguments"] = "{}"
            result.append(slot)
        return result

    def _parse_full(self, resp) -> Iterator[Dict[str, Any]]:
        raw = resp.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise MiMoError(f"无法解析接口返回内容：{raw[:300]}") from None

        if payload.get("error"):
            err = payload["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise MiMoError(f"接口返回错误：{msg}")

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""

        tool_calls = []
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            func = call.get("function") or {}
            tool_calls.append(
                {
                    "id": call.get("id") or f"call_{len(tool_calls)}",
                    "type": call.get("type") or "function",
                    "function": {
                        "name": func.get("name") or "",
                        "arguments": func.get("arguments") or "{}",
                    },
                }
            )

        if reasoning:
            yield {"type": "reasoning", "text": reasoning}
        if content:
            yield {"type": "content", "text": content}
        if tool_calls:
            yield {"type": "tool_calls", "tool_calls": tool_calls}
        if payload.get("usage"):
            yield {"type": "usage", "usage": payload["usage"]}
        yield {
            "type": "done",
            "content": content,
            "reasoning": reasoning,
            "tool_calls": tool_calls,
            "usage": payload.get("usage") or {},
            "finish_reason": choice.get("finish_reason") or "",
            "model": payload.get("model") or self.model,
        }

    # ---------------------------------------------------------------- 工具 --

    def ping(self) -> str:
        """连通性自检：发一条最短的消息，返回模型的回复文本。"""
        text = ""
        for event in self.chat(
            [{"role": "user", "content": "ping"}],
            stream=False,
            params={"max_completion_tokens": 16, "temperature": 1.0, "top_p": 0.95},
        ):
            if event["type"] == "content":
                text += event["text"]
        return text.strip()

    def list_models(self) -> List[str]:
        """尝试拉取模型列表（部分网关未实现该接口，失败时抛 MiMoError）。"""
        url = f"{self.base_url}/models"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with self._opener.open(req, timeout=min(self.timeout, 30)) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
            raise MiMoError(_friendly_http_error(exc.code, body), status=exc.code, body=body) from None
        except Exception as exc:  # noqa: BLE001
            raise MiMoError(f"获取模型列表失败：{exc}") from None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise MiMoError("接口未返回标准模型列表")
        ids = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
            elif isinstance(item, str):
                ids.append(item)
        return ids


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #

def ask(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    system: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """一次性问答（同步返回完整文本），方便在别的脚本里直接调用。"""
    key = api_key or os.environ.get("MIMO_API_KEY", "")
    client = MiMoClient(api_key=key, base_url=base_url, model=model)
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    text = ""
    for event in client.chat(messages, stream=False):
        if event["type"] == "content":
            text += event["text"]
    return text
