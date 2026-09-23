# -*- coding: utf-8 -*-
"""
示例技能：文本统计

这是「代码技能」的写法——TOOL 描述给模型看，run() 是真正执行的函数。
复制这个目录改一改，就是一个新技能，不需要注册、不需要改任何其他代码。
"""

import re

TOOL = {
    "name": "text_stats",
    "description": (
        "统计一段文本的规模：字符数、中文字数、英文单词数、行数、段落数，"
        "并给出出现次数最多的若干词语。适合分析文章、日志、用户反馈的长度与关键词分布。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要统计的文本内容"},
            "top_words": {"type": "integer", "description": "返回出现次数最多的前 N 个词，默认 5"},
        },
        "required": ["text"],
    },
}


def run(text: str, top_words: int = 5) -> str:
    content = text or ""
    chinese = re.findall(r"[\u4e00-\u9fff]", content)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", content)
    lines = content.split("\n") if content else []
    paragraphs = [p for p in re.split(r"\n\s*\n", content) if p.strip()]

    # 中文按双字切、英文按词切，粗略统计高频词
    tokens = re.findall(r"[\u4e00-\u9fff]{2}", content) + [w.lower() for w in words if len(w) > 2]
    counter = {}
    for token in tokens:
        counter[token] = counter.get(token, 0) + 1

    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[: max(1, int(top_words or 5))]
    top_text = "、".join(f"{w}×{n}" for w, n in top) if top else "（无明显高频词）"

    return (
        f"字符数：{len(content)}\n"
        f"中文字数：{len(chinese)}\n"
        f"英文单词数：{len(words)}\n"
        f"行数：{len(lines)}\n"
        f"段落数：{len(paragraphs)}\n"
        f"高频词：{top_text}"
    )
