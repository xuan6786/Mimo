# -*- coding: utf-8 -*-
"""
mimo_tools.agent —— 带工具调用的多轮对话循环

一次 run() 的流程：

    用户消息 ──▶ 模型（带 tools）
                   │
                   ├── 直接给答案 ──▶ 结束
                   │
                   └── 返回 tool_calls
                          │
                          ▼
                    本地执行工具（builtin / skill / mcp 一视同仁）
                          │
                          ▼
                    把结果作为 role=tool 消息回灌 ──▶ 再问模型（循环）

对外只暴露一串事件，命令行和网页各自决定怎么展示：

    {"type": "round",        "index": int, "max": int}
    {"type": "reasoning",    "text": str}
    {"type": "content",      "text": str}
    {"type": "tool_call",    "id": str, "name": str, "arguments": dict, "source": str}
    {"type": "tool_result",  "id": str, "name": str, "ok": bool, "content": str, "elapsed": float}
    {"type": "usage",        "usage": dict}
    {"type": "retry",        ...}
    {"type": "done",         "content": str, "reasoning": str, "usage": dict,
                             "rounds": int, "tool_calls": list, "finish_reason": str, "model": str}
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from .base import ToolRegistry

__all__ = ["AgentRunner"]

DEFAULT_MAX_ITERATIONS = 6


class AgentRunner:
    def __init__(
        self,
        client: Any,
        registry: Optional[ToolRegistry] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        tool_choice: str = "auto",
    ):
        self.client = client
        self.registry = registry if registry is not None else ToolRegistry()
        self.max_iterations = max(1, int(max_iterations))
        self.tool_choice = tool_choice or "auto"

    @property
    def tool_count(self) -> int:
        return len(self.registry)

    # ------------------------------------------------------------------ #

    def run(
        self,
        messages: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        stream: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        conversation: List[Dict[str, Any]] = [dict(m) for m in messages]
        params = dict(params or {})
        base_extra = dict(extra_body or {})

        total_usage: Dict[str, Any] = {}
        tool_log: List[Dict[str, Any]] = []
        last_content = ""
        last_reasoning = ""
        last_finish = ""
        last_model = ""
        rounds = 0

        use_tools = len(self.registry) > 0
        # 多留一轮用于收口：前 max_iterations 轮可以调工具，最后一轮不给工具、只出答案
        total_rounds = self.max_iterations + 1 if use_tools else 1

        for round_index in range(total_rounds):
            rounds = round_index + 1
            allow_tools = use_tools and round_index < self.max_iterations
            extra = dict(base_extra)
            if allow_tools:
                extra["tools"] = self.registry.schemas()
                extra.setdefault("tool_choice", self.tool_choice)
            else:
                extra.pop("tools", None)
                extra.pop("tool_choice", None)

            if use_tools and rounds > 1:
                yield {"type": "round", "index": rounds, "max": total_rounds}
                if not allow_tools:
                    yield {
                        "type": "notice",
                        "level": "warn",
                        "message": f"工具调用轮次已达上限（{self.max_iterations} 轮），正在要求模型直接给出最终回答。",
                    }

            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            usage: Dict[str, Any] = {}
            finish_reason = ""

            for event in self.client.chat(conversation, stream=stream, params=params, extra_body=extra):
                etype = event.get("type")
                if etype == "content":
                    content_parts.append(event.get("text") or "")
                    yield event
                elif etype == "reasoning":
                    reasoning_parts.append(event.get("text") or "")
                    yield event
                elif etype == "tool_call_start":
                    yield event
                elif etype == "tool_calls":
                    tool_calls = event.get("tool_calls") or []
                elif etype == "usage":
                    usage = event.get("usage") or {}
                elif etype == "retry":
                    yield event
                elif etype == "done":
                    tool_calls = event.get("tool_calls") or tool_calls
                    usage = event.get("usage") or usage
                    finish_reason = event.get("finish_reason") or ""
                    last_model = event.get("model") or last_model
                    if event.get("content") and not content_parts:
                        content_parts.append(event["content"])
                    if event.get("reasoning") and not reasoning_parts:
                        reasoning_parts.append(event["reasoning"])

            content = "".join(content_parts)
            reasoning = "".join(reasoning_parts)
            last_content = content or last_content
            last_reasoning = reasoning or last_reasoning
            last_finish = finish_reason or last_finish
            self._merge_usage(total_usage, usage)

            if not tool_calls:
                break

            # 把这一轮的 assistant 消息（含 tool_calls）写回上下文
            assistant_message: Dict[str, Any] = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            if reasoning:
                # 官方建议：多轮工具调用时保留 reasoning_content
                assistant_message["reasoning_content"] = reasoning
            conversation.append(assistant_message)

            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name") or ""
                raw_args = function.get("arguments") or "{}"
                call_id = call.get("id") or f"call_{len(tool_log)}"

                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    if not isinstance(arguments, dict):
                        arguments = {"value": arguments}
                except json.JSONDecodeError:
                    arguments = {}
                    yield {
                        "type": "tool_result",
                        "id": call_id,
                        "name": name,
                        "ok": False,
                        "content": f"错误：模型给出的参数不是合法 JSON：{str(raw_args)[:200]}",
                        "elapsed": 0.0,
                    }
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"错误：参数不是合法 JSON，请重新给出正确的 JSON 参数。收到：{str(raw_args)[:200]}",
                        }
                    )
                    tool_log.append({"id": call_id, "name": name, "ok": False, "arguments": {}})
                    continue

                tool = self.registry.get(name)
                yield {
                    "type": "tool_call",
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "source": tool.source if tool else "unknown",
                }

                result = self.registry.call(name, arguments)
                yield {
                    "type": "tool_result",
                    "id": call_id,
                    "name": name,
                    "ok": result.ok,
                    "content": result.content,
                    "elapsed": result.elapsed,
                }

                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result.content,
                    }
                )
                tool_log.append(
                    {
                        "id": call_id,
                        "name": name,
                        "ok": result.ok,
                        "arguments": arguments,
                        "elapsed": round(result.elapsed, 3),
                    }
                )

        yield {
            "type": "done",
            "content": last_content,
            "reasoning": last_reasoning,
            "usage": total_usage,
            "rounds": rounds,
            "tool_calls": tool_log,
            "finish_reason": last_finish,
            "model": last_model,
        }

    @staticmethod
    def _merge_usage(total: Dict[str, Any], usage: Dict[str, Any]) -> None:
        if not usage:
            return
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                total[key] = int(total.get(key) or 0) + int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
