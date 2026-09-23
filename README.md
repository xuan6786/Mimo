# MiMo 本地助手

在本地直接调用 **Xiaomi MiMo API**（小米 MiMo 大模型开放平台）。提供两种使用方式：

- **网页版可视化界面**（推荐）—— 浏览器里的聊天界面，流式输出、Markdown 渲染、思考过程折叠、多会话管理
- **命令行版** —— 终端里的交互式对话，适合脚本调用

两种方式都 **零第三方依赖**，只用 Python 标准库，不需要 `pip install` 任何东西。

另外内置了一条 **工具扩展总线**：模型可以调用内置工具、本地技能（Skill），
也能接上外部的 **MCP Server**，三种来源统一注册、统一执行。详见 [第四节](#四工具扩展内置工具--skill--mcp)。

---

## 一、快速开始

### 1. 确认 Python 版本

需要 Python 3.8 及以上：

```bash
python --version
```

### 2. 配置 API Key

先到 [MiMo 开放平台控制台](https://platform.xiaomimimo.com/#/console/api-keys) 创建 API Key（按量付费格式为 `sk-xxxxx`）。

三选一：

| 方式 | 操作 |
| --- | --- |
| 环境变量 | Windows CMD：`set MIMO_API_KEY=sk-xxxxx`<br>PowerShell：`$env:MIMO_API_KEY="sk-xxxxx"`<br>Git Bash / macOS / Linux：`export MIMO_API_KEY=sk-xxxxx` |
| 配置文件 | 编辑 `config.json` 的 `"api_key"` 字段 |
| 网页界面 | 启动后在右上角「设置」里填写并保存 |

### 3. 启动

**网页版（推荐）**

```bash
python server.py
```

浏览器会自动打开 <http://127.0.0.1:8765>。Windows 也可直接双击 **`启动网页版.bat`**。

**命令行版**

```bash
python assistant.py
```

Windows 可双击 **`启动助手.bat`**。

### 4. 自检

```bash
python assistant.py --check      # 命令行自检
```

网页版在「设置」里点 **测试连接** 即可。

---

## 二、网页版界面

```
python server.py                       # 默认 127.0.0.1:8765，自动打开浏览器
python server.py --port 9000           # 换端口
python server.py --no-browser          # 不自动打开浏览器
python server.py --config other.json   # 使用别的配置文件
```

> 服务默认只监听 `127.0.0.1`，局域网内其他机器访问不到。如果确实需要，用 `--host 0.0.0.0`，但要清楚这意味着同网段的人都能用你的额度。

### 界面功能

| 区域 | 功能 |
| --- | --- |
| 左侧栏 | 多会话列表、新建对话、删除对话、累计 token 统计、主题切换 |
| 顶部栏 | 会话标题（可改）、模型切换、导出 Markdown、清空当前对话 |
| 消息区 | 流式打字机输出、Markdown 渲染（标题/列表/表格/引用/代码块）、代码高亮 + 一键复制、思考过程可折叠、**工具调用卡片**（参数与返回值可展开） |
| 底部输入 | Enter 发送 / Shift+Enter 换行、工具开关、联网搜索开关、显示思考开关、回传思考开关、生成中可「停止」 |
| 设置面板 | API Key、Base URL、默认模型、temperature / top_p / 最大输出 tokens、系统提示词、超时、重试次数、代理、测试连接、**工具扩展（清单 + 试跑 + 重载）** |

### 交互细节

- **思考过程**：模型返回 `reasoning_content` 时，正文上方会出现一个可折叠的「已深度思考（N 字）」区块，生成过程中自动展开，完成后自动收起
- **停止生成**：生成过程中发送按钮变成「停止」，点击即中断（`Esc` 也可以）
- **重新生成**：鼠标移到回复上，点刷新图标即可重答
- **会话自动保存**：所有对话存在浏览器 localStorage，刷新不丢；可随时导出为 Markdown
- **快捷键**：`Enter` 发送、`Shift+Enter` 换行、`Ctrl/Cmd + K` 新对话、`Esc` 停止 / 关弹窗
- **主题**：默认跟随系统，可手动切换浅色 / 深色

---

## 三、命令行版

```bash
python assistant.py                        # 交互式对话
python assistant.py -p "用一句话解释量子纠缠"   # 单次问答后退出
python assistant.py --model mimo-v2.6-flash
python assistant.py --no-stream
python assistant.py --check                # 连通性自检
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-p, --prompt` | 单次提问，输出后退出 |
| `-m, --model` | 指定模型 |
| `--api-key` | 临时指定 API Key（不写入配置文件） |
| `--base-url` | 指定接口地址 |
| `--system` | 临时指定系统提示词 |
| `--temperature` | 采样温度 |
| `--max-tokens` | 最大输出 token 数 |
| `--no-stream` | 关闭流式输出 |
| `--no-color` | 关闭彩色输出 |
| `--config` | 使用别的配置文件 |

### 交互式命令

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示全部命令 |
| `/new` | 开启新对话，清空上下文 |
| `/model [名称]` | 查看 / 切换模型 |
| `/models` | 列出可用模型 |
| `/system [内容]` | 查看 / 设置角色设定 |
| `/tools [on\|off]` | 查看已装配的工具（内置 / 技能 / MCP），或开关工具调用 |
| `/tool 名字 {json}` | 手动执行一个工具，如 `/tool get_current_time {}` |
| `/set 键=值` | 设置请求参数，如 `/set temperature=0.6` |
| `/unset 键` | 移除某个参数 |
| `/params` | 查看当前请求参数 |
| `/stream on\|off` | 开 / 关流式输出 |
| `/think on\|off` | 开 / 关思考过程显示 |
| `/history` | 查看当前上下文 |
| `/undo` | 撤销上一轮问答 |
| `/retry` | 重新生成上一次回复（含失败重试） |
| `/save [文件名]` | 保存对话到 `sessions/` |
| `/load [文件名]` | 载入已保存的对话 |
| `/sessions` | 列出所有存档 |
| `/usage` | 查看累计 token 用量 |
| `/config` | 查看当前配置（Key 自动打码） |
| `/clear` | 清屏 |
| `/exit` | 退出（自动存档） |

**小技巧**：行尾加 `\` 可多行输入；生成中按 `Ctrl+C` 可中断，已生成的部分会保留；`/set` 里非标准采样参数会原样透传给接口，比如 `/set forced_search=true` 开联网搜索。

---

## 四、工具扩展（内置工具 / Skill / MCP）

助手内置了一条**统一的能力总线**：三种来源的工具都会被转成同一种形状（OpenAI `tools` 规范），
模型侧完全感知不到区别，你新增能力时也不需要改动对话逻辑。

```
                       ┌──────────────────────┐
   内置工具 builtin  ──▶│                      │
   本地技能 skill    ──▶│    ToolRegistry      │──▶ 模型看到的 tools 列表
   MCP Server       ──▶│  （统一注册 / 执行）  │
                       └──────────────────────┘
                                 │
                      AgentRunner 多轮循环：
                      模型要工具 → 本地执行 → 结果回灌 → 再问模型
```

**默认是关闭的**，需要显式打开：

```bash
python assistant.py --tools          # 命令行临时开启
# 或把 config.json 里 tools.enabled 改成 true 永久开启
```

网页版在「设置 → 工具扩展」里勾选「启用工具调用」，输入框上方的「工具」标签可以随时临时关掉。

### 4.1 内置工具

| 工具 | 说明 |
| --- | --- |
| `get_current_time` | 获取当前日期时间 |
| `list_directory` | 列出目录内容 |
| `read_text_file` | 读取文本文件（支持行号范围） |
| `write_text_file` | 写入文本文件（**默认关闭**，需开 `allow_write`） |
| `http_get` | 抓取网页/接口文本 |

**安全边界**：所有文件操作都被限制在 `tools.sandbox_root`（默认是项目目录）内，
`../` 之类的越界路径会被直接拒绝；写文件默认关闭；联网可用 `allow_network` 关掉。

> 注意：`read_text_file` 能读到沙箱内的任何文件，**包括 config.json 里的 API Key**。
> 如果你不希望模型有机会读到密钥，请把沙箱根目录设成别的位置，或干脆别开内置工具。

### 4.2 本地技能（Skill）

把技能放进 `skills/` 目录，启动时自动加载，**不用注册、不用改代码**。

**代码技能** → 变成模型可调用的工具：

```
skills/我的技能/
├── SKILL.md      # 可选，人类可读的说明
└── skill.py      # 必需
```

```python
TOOL = {
    "name": "word_count",
    "description": "统计文本字数。模型靠这句话判断什么时候该调用",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "待统计文本"}},
        "required": ["text"],
    },
}

def run(text: str) -> str:
    return f"共 {len(text)} 个字符"
```

一个技能可以暴露多个工具（`TOOL` 写成列表，入口函数默认与工具同名）。
返回值可以是字符串、字典、列表；抛异常不会中断对话，会被转成错误信息让模型自己纠正。

**提示词技能** → 不产生工具，只把 `SKILL.md` 正文拼进系统提示词，
适合放「回答风格」「代码规范」「术语表」这类软约束。

仓库里已带三个示例：`text-stats`（代码技能）、`json-tool`（一个技能两个工具）、`answer-style`（提示词技能）。
详细写法见 [`skills/README.md`](skills/README.md)。

### 4.3 MCP Server

在 `mcp.json` 里配置，支持 **stdio** 与 **Streamable HTTP** 两种传输：

```jsonc
{
  "servers": {
    "filesystem": {
      "enabled": true,
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "Y:/A_MiMo"],
      "timeout": 40
    },
    "remote": {
      "enabled": false,
      "transport": "http",
      "url": "http://127.0.0.1:3000/mcp",
      "headers": {"Authorization": "Bearer xxx"}
    }
  }
}
```

助手会依次完成 `initialize` → `notifications/initialized` → `tools/list`，
把每个 MCP 工具桥接进注册表（描述会带上 `[MCP:服务器名]` 前缀）。
MCP Server 通过 `instructions` 返回的使用说明也会被拼进系统提示词。

工具重名时自动加服务器名前缀（如 `filesystem__read_file`），不会互相覆盖。

### 4.4 调试

```bash
/tools                              # 命令行：列出已装配的工具与来源
/tool get_current_time {}           # 命令行：手动执行一次
```

网页版「设置 → 工具扩展」里有工具清单，**点任意一行可以试跑**，方便验证技能和 MCP 是否正常。

### 4.5 相关配置

```jsonc
"tools": {
  "enabled": false,          // 总开关
  "max_iterations": 6,       // 一次对话最多几轮工具调用（最后一轮强制出答案）
  "sandbox_root": "",        // 内置工具的沙箱根目录，留空 = 项目目录
  "builtin": { "enabled": true, "allow_write": false, "allow_network": true },
  "skills":  { "enabled": true, "dir": "skills", "include": [], "exclude": [] },
  "mcp":     { "enabled": true, "config": "mcp.json", "include": [], "exclude": [] }
}
```

---

## 五、配置项说明（config.json）

网页版和命令行版共用同一个 `config.json`。

```jsonc
{
  "api_key": "",                                   // 留空则读环境变量 MIMO_API_KEY
  "base_url": "https://api.xiaomimimo.com/v1",     // 接口地址
  "model": "mimo-v2.6-pro",                        // 默认模型
  "system_prompt": "你是MiMo……{date} {week}……",     // 支持 {date} {week} 占位符
  "stream": true,                                  // 是否流式输出
  "show_reasoning": true,                          // 是否显示思考过程
  "timeout": 180,                                  // 单次请求超时（秒）
  "max_retries": 3,                                // 失败重试次数（指数退避）
  "auth_scheme": "both",                           // api-key / bearer / both
  "proxy": "",                                     // 代理，留空则读系统环境变量
  "params": {
    "temperature": 1.0,
    "top_p": 0.95,
    "max_completion_tokens": 4096
  },
  "extra_body": {}                                 // 额外请求参数，原样透传给接口
}
```

### base_url 怎么选

| 计费方式 | OpenAI 兼容地址 | Key 格式 |
| --- | --- | --- |
| 按量付费 | `https://api.xiaomimimo.com/v1` | `sk-xxxxx` |
| Token Plan | `https://token-plan-cn.xiaomimimo.com/v1` | `tp-xxxxx`（个人）/ `ttp-xxxxx`（团队） |
| 批量推理 | `https://batch-api-cn.xiaomimimo.com/v1` | `sk-xxxxx` |

> Token Plan 的 Key 与按量付费的 Key 相互独立、不可混用。

### 可用模型

| 模型 ID | 说明 |
| --- | --- |
| `mimo-v2.6-pro` | V2.6 旗舰推理模型，综合能力最强 |
| `mimo-v2.6-pro-ultraspeed` | V2.6 旗舰极速版，低延迟高吞吐 |
| `mimo-v2.6-flash` | V2.6 轻量版，日常对话 / 高并发性价比高 |
| `mimo-v2.5-pro` / `mimo-v2.5` | V2.5 系列，官方公告将于 2026-10-21 下线 |

> 思考模式下，`temperature` 和 `top_p` 会被模型强制为默认值 `1.0` / `0.95`。
> 当前仅 `mimo-v2.6-flash`、`mimo-v2.6-pro`、`mimo-v2.6-pro-ultraspeed`、`mimo-v2.5-pro`、`mimo-v2.5` 支持联网搜索。

---

## 六、在别的 Python 脚本里调用

`mimo_client.py` 可以独立当 SDK 用：

```python
from mimo_client import MiMoClient, ask

# 简单一次性调用
print(ask("你好", api_key="sk-xxxxx", model="mimo-v2.6-pro"))

# 流式调用 + 思考过程
client = MiMoClient(api_key="sk-xxxxx")
for event in client.chat([{"role": "user", "content": "讲个笑话"}]):
    if event["type"] == "reasoning":
        print("[思考]", event["text"], end="")
    elif event["type"] == "content":
        print(event["text"], end="", flush=True)
    elif event["type"] == "done":
        print("\n用量:", event["usage"])
```

---

## 七、目录结构

```
A_MiMo/
├── server.py            # 网页版本地服务（静态托管 + API 代理 + SSE 流式转发）
├── assistant.py         # 命令行助手主程序
├── mimo_client.py       # MiMo API 客户端，可独立作为 SDK 使用
├── mimo_tools/          # 工具扩展层
│   ├── base.py          #   Tool / ToolResult / ToolRegistry 抽象
│   ├── builtin.py       #   内置工具（沙箱内文件操作、时间、联网）
│   ├── skills.py        #   本地技能加载器
│   ├── mcp.py           #   MCP 客户端（stdio / http）
│   └── agent.py         #   AgentRunner 多轮工具调用循环
├── web/                 # 网页界面（原生 HTML/CSS/JS，无第三方库）
│   ├── index.html
│   ├── style.css
│   └── app.js
├── skills/              # 技能目录，放进来就会被自动加载
│   ├── README.md        #   技能编写指南
│   ├── text-stats/      #   示例：代码技能
│   ├── json-tool/       #   示例：一个技能多个工具
│   └── answer-style/    #   示例：提示词技能
├── config.json          # 配置文件（两种方式共用）
├── mcp.json             # MCP Server 配置
├── 启动网页版.bat        # Windows 一键启动网页版
├── 启动助手.bat          # Windows 一键启动命令行版
├── README.md            # 本文档
└── sessions/            # 命令行版的对话存档目录（首次保存时自动创建）
```

---

## 八、常见问题

**Q：提示 `缺少 API Key` / 页面顶部黄色提示**
A：按上文第 2 步配置，或在网页右上角「设置」里填写 API Key 后保存。

**Q：报 `401 / 403`**
A：401 是 Key 无效或未授权；403 是账号未开通该模型或 Token Plan 已过期。

**Q：报 `404`**
A：`base_url` 写错了，注意按量付费和 Token Plan 的地址不同，且末尾要带 `/v1`。

**Q：报 `429`**
A：触发限流或额度不足，稍等重试或到控制台查看余额。

**Q：报 `402 Insufficient account balance`**
A：账户余额不足，需要到 MiMo 控制台充值，或改用 Token Plan 订阅。这个错误不会重试，会直接返回。

**Q：连不上 / 一直超时**
A：多为网络问题。如果本地有代理，在设置里填代理地址（如 `http://127.0.0.1:7890`）。

**Q：网页打开是空白 / 端口被占用**
A：换端口启动 `python server.py --port 9000`；若界面空白，检查浏览器控制台是否有报错。

**Q：中文显示乱码**
A：Windows 终端执行 `chcp 65001`；用 `.bat` 启动会自动处理。

**Q：网页版的对话存在哪？**
A：存在浏览器 localStorage（自动保存），可导出为 Markdown。命令行版的存档在 `sessions/`。两者互不影响。

**Q：怎么让模型开启联网搜索？**
A：网页版打开输入框上方的「联网搜索」开关；命令行版 `/set forced_search=true`。

**Q：工具调用没生效 / 模型不调工具**
A：先确认开关：命令行看启动横幅的「工具」一栏，网页版看设置里的「工具扩展」。
再用 `/tools`（或网页版设置面板）确认工具确实加载了。都正常的话，多半是模型判断这个问题不需要工具。

**Q：加了技能但没被加载**
A：检查目录结构（`skills/名字/skill.py`）、`TOOL` 字典格式、入口函数名。
加载失败的原因会出现在 `/tools` 的提示里，不会静默吞掉。

**Q：MCP Server 连不上**
A：`/tools` 或网页版设置面板会显示具体原因（命令不存在、进程退出、响应超时，都会带上 stderr 片段）。
stdio 传输需要本机装好对应运行时，比如 `npx` 需要 Node.js。

**Q：工具会不会乱动我的文件？**
A：内置文件工具被限制在 `tools.sandbox_root` 内，越界直接拒绝；写文件默认关闭。
但沙箱内的文件模型都能读——包括 `config.json`。不放心就把沙箱指到别处，或关掉内置工具。

---

## 九、接口参考

- 快速开始：<https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call>
- 接入 FAQ：<https://mimo.mi.com/docs/zh-CN/quick-start/faq/api-integration>
- 模型超参：<https://mimo.mi.com/docs/zh-CN/quick-start/model-hyperparameters>
- 控制台：<https://platform.xiaomimimo.com/>

协议：兼容 OpenAI Chat Completions（`POST {base_url}/chat/completions`），鉴权支持 `api-key: xxx` 或 `Authorization: Bearer xxx`。

### 网页版内部接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/config` | 读取配置（API Key 打码返回） |
| POST | `/api/config` | 写入配置（字段白名单校验） |
| POST | `/api/test` | 连通性自检 |
| POST | `/api/chat` | 对话，SSE 流式返回 `start / reasoning / content / tool_call / tool_result / round / notice / usage / retry / done / error / end` 事件 |
| GET | `/api/tools` | 列出已装配的工具、提示词技能与加载日志 |
| POST | `/api/tools/reload` | 强制重建工具（重新连 MCP） |
| POST | `/api/tool/call` | 手动执行一个工具，用于调试 |
| GET | `/api/models` | 尝试拉取模型列表 |
