# 技能目录

把技能放进这个目录，助手启动时会自动扫描并加载。**不需要注册、不需要改代码。**

## 两种技能

### 1. 代码技能 —— 变成模型可调用的工具

```
skills/
└── 我的技能/
    ├── SKILL.md      # 可选，人类可读的说明
    └── skill.py      # 必需，工具定义 + 实现
```

`skill.py` 的最小写法：

```python
TOOL = {
    "name": "my_tool",
    "description": "这个工具是干什么的，什么时候该用——模型靠这句话决定要不要调用",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "参数说明"},
        },
        "required": ["text"],
    },
}

def run(text: str) -> str:
    return f"收到 {len(text)} 个字符"
```

一个技能想暴露多个工具时，`TOOL` 写成列表。此时入口函数默认取**与工具同名的函数**，
也可以用 `handler` 字段显式指定：

```python
TOOL = [
    {"name": "tool_a", "description": "...", "parameters": {...}},
    {"name": "tool_b", "description": "...", "parameters": {...}, "handler": "do_b"},
]

def tool_a(...): ...   # 名字与工具名一致，自动匹配
def do_b(...): ...     # 用 handler 指定
```

规则：

- `run()` 的返回值可以是字符串、字典、列表，会被自动转成文本回灌给模型
- 抛异常不会中断对话，会被转成「工具执行失败」的信息交给模型自己纠正
- 模型多传了参数会被自动忽略，不用写 `**kwargs`
- 参数默认值写在函数签名里即可
- 工具名只允许字母、数字、下划线、连字符，最长 64 字符

### 2. 提示词技能 —— 只改系统提示词，不产生工具

目录里**只有** `SKILL.md` 时，它会被当成提示词技能：正文会被拼进系统提示词。
适合放「回答风格」「代码规范」「领域术语表」这类软约束。

```
skills/
└── 回答风格/
    └── SKILL.md
```

```markdown
---
name: 回答风格
description: 一句话说明
---

（这里写具体的规则，会被原样拼进系统提示词）
```

`---` 包起来的是 frontmatter，只支持扁平的 `key: value`，不需要装 PyYAML。

## 单文件技能

不想建文件夹也行，直接放 `skills/xxx.py`，规则和 `skill.py` 完全一样。
配套说明可以放在 `skills/xxx.md`。

## 开关与筛选

`config.json`：

```jsonc
"tools": {
  "enabled": true,            // 总开关，默认 false
  "max_iterations": 6,        // 一次对话最多允许几轮工具调用
  "skills": {
    "enabled": true,
    "dir": "skills",          // 也可以指向别的目录
    "include": [],            // 白名单，留空表示全部加载
    "exclude": ["answer-style"]  // 黑名单
  }
}
```

## 调试

命令行版：`/tools` 列出已加载的工具，`/tool 工具名 {"参数": "值"}` 手动执行一次。
网页版：设置面板里能看到工具清单，出错信息也会显示在那里。
