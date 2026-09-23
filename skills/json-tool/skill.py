# -*- coding: utf-8 -*-
"""
示例技能：JSON 工具

演示「一个技能提供多个工具」的写法——TOOL 可以是一个列表。
"""

import json

TOOL = [
    {
        "name": "json_format",
        "description": "校验并格式化一段 JSON 文本，缩进 2 空格输出。如果 JSON 非法，会指出错误位置。",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "待校验的 JSON 文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "json_query",
        "description": "从 JSON 文本中按路径取值。路径用点号分隔，数组下标写在方括号里，例如 data.items[0].name。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "JSON 文本"},
                "path": {"type": "string", "description": "取值路径，例如 data.items[0].name"},
            },
            "required": ["text", "path"],
        },
    },
]


def _parse(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 非法：第 {exc.lineno} 行第 {exc.colno} 列 —— {exc.msg}") from None


def json_format(text: str) -> str:
    data = _parse(text)
    return json.dumps(data, ensure_ascii=False, indent=2)


def json_query(text: str, path: str) -> str:
    data = _parse(text)
    cursor = data
    for raw in _split_path(path or ""):
        if isinstance(raw, int):
            if not isinstance(cursor, list):
                raise ValueError(f"路径 {path} 处期望数组，实际是 {type(cursor).__name__}")
            if raw >= len(cursor):
                raise ValueError(f"数组下标越界：{raw}（长度 {len(cursor)}）")
            cursor = cursor[raw]
        else:
            if not isinstance(cursor, dict):
                raise ValueError(f"路径 {path} 处期望对象，实际是 {type(cursor).__name__}")
            if raw not in cursor:
                raise ValueError(f"键不存在：{raw}。可用键：{', '.join(cursor.keys()) or '（空）'}")
            cursor = cursor[raw]
    if isinstance(cursor, (dict, list)):
        return json.dumps(cursor, ensure_ascii=False, indent=2)
    return json.dumps(cursor, ensure_ascii=False)


def _split_path(path):
    """把 a.b[0].c 拆成 ['a', 'b', 0, 'c']。"""
    parts = []
    for chunk in str(path).replace("[", ".[").split("."):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("[") and chunk.endswith("]"):
            index = chunk[1:-1].strip()
            if not index.isdigit():
                raise ValueError(f"数组下标必须是数字：{chunk}")
            parts.append(int(index))
        else:
            parts.append(chunk)
    return parts
