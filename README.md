# whut-2api

把武汉理工的 "DeepSeek云服务版"包装成**标准协议接口**，让 pi、Claude Code、Codex CLI、Cline、Cherry Studio 或任何 OpenAI SDK 都能直接用上学校的模型。

[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](.) [![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](.) [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

---

## 原理

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Song-xuan-xuan/wut-deepseek2api/main/web/public/architecture-dark.svg">
  <img src="https://raw.githubusercontent.com/Song-xuan-xuan/wut-deepseek2api/main/web/public/architecture-light.svg" alt="whut-2api 工作原理：AI 客户端 → 本地代理（注入 token / 续期 / 统计 / 分发）→ 学校 OpenWebUI → DeepSeek" width="100%">
</picture>

### 为什么需要这一层

这套 OpenWebUI **本来就提供 OpenAI 格式的接口**（`POST /api/chat/completions`），所以不需要逆向网页、不需要浏览器自动化。
真正麻烦的只有两个：

1. **个人 API Key 被管理员关了**（`enable_api_key: false`）→ 只能用浏览器登录后那份 JWT；
2. 这份 JWT **7 天过期**，而且是一大串 `eyJhbGciOi...`——直接塞进各个 AI 工具，每 7 天要改一遍配置，还没法统一做参数修正。

于是有了这层本地代理：**把「凭证」和「协议修正」从每个客户端里收出来，集中在一处处理。**

### 一次请求发生了什么

| 步骤 | 发生了什么 |
|---|---|
| ① | 客户端用**本地假 key**（`sk-whut-local`）请求 `http://127.0.0.1:8787/v1/...` |
| ② | 代理**注入真实的学校 JWT**（从 `data/token` 读，按修改时间热加载，换 token 不用重启） |
| ③ | 代理**修正参数**：`max_completion_tokens → max_tokens`、钳制输出上限、剔除 `store`/`metadata` 等上游不认识的字段 |
| ④ | 转发到 `https://chatcloud.whut.edu.cn/api/chat/completions`，再转给 DeepSeek |
| ⑤ | 响应**字节级透传**回来（流式不拆包、不重组），同时把 usage / 耗时**落库**到 `data/usage.db`（SQLite） |
| ⑥ | 客户端拿到的就是标准响应：OpenAI JSON / SSE、Anthropic 事件流或 Responses 事件流，工具调用原样可用 |

### 三个协议入口，同一份上游

| 入口 | 给谁用 | 说明 |
|---|---|---|
| `POST /v1/chat/completions` | pi · Cline · Cherry · Continue · OpenAI SDK | 原生支持 `tools` / `tool_calls`，流式增量 |
| `POST /v1/messages` | **Claude Code** · Anthropic SDK | 把 OpenAI delta 翻译成 Anthropic 事件序列，含 `thinking` 块与 `count_tokens` |
| `POST /v1/responses` | **Codex CLI**（0.1xx 起只支持 Responses API） | 输出 `output` 数组 + 事件流，兼容 `wire_api = "responses"` |

三个入口共用同一个 provider，所以**限速、熔断、重试、用量统计、分发 key 全都一致**。

### 代理兜住的四件事

动画图中间的四个模块：

| 模块 | 作用 | 关键实现 |
|---|---|---|
| **① 转发 proxy** | 协议转换 + 参数修正 | `max_tokens` 归一与钳制、字段白名单剔除、SSE 透传 |
| **② 续期 auth** | token 永不手动维护 | 启动时 + 每 60 分钟检查，剩余 < 2 天自动换；被 401/403 时当场换并重试原请求 |
| **③ 统计 usage** | 知道用了多少 | SQLite 落库（重启不清零），按模型 / 按 key / 按小时聚合，CSV 导出 |
| **④ 分发 access** | 多设备各用各的 | 独立 key、每分钟限速、每日 token 配额、随时吊销，用量分开统计 |

### 为什么续期不需要密码

实测（2026-11）：学校的 `POST /api/v1/auths/signin` 只校验**学号是否存在**，`password` 字段只要非空即可通过。
所以本项目只需要学号，从不保存也不需要你的密码。

---

## 安装教程

> **共同前提：必须能访问校园网**（校内 / 学校 VPN）。校外直连会超时。
> 若本机开着 Clash / Surge 等代理，请把 `whut.edu.cn` 加进**直连规则**——学校会拒绝代理节点的出口 IP。

**懒人方案**：把下面这段话直接发给你自己的 AI agent（pi / Claude Code / Cline 都行），让它替你装：

```text
帮我在本机安装并部署 https://github.com/Song-xuan-xuan/wut-deepseek2api 这个项目：
1. git clone 到当前目录，用 python3 -m venv .venv 建虚拟环境，安装 requirements.txt
2. 复制 config.example.json 为 config.json
3. 后台启动 .venv/bin/python -m app（macOS/Linux），或 .venv\Scripts\python -m app（Windows）
4. 确认 http://127.0.0.1:8787/health 返回 200，并把面板地址 http://127.0.0.1:8787/dashboard 告诉我
5. 提醒我在面板「认证」页填学号完成认证（不需要密码）
6. 如果端口 8787 被占用、依赖装不上、或代理导致连不上学校，请自行排查并汇报原因
注意：必须先能访问校园网；token 和数据库都保存在项目内的 data/ 目录。
```

### 一、本地开发

适合：想改代码、调面板，或机器上不方便跑 Docker。

**① 准备环境**

| 依赖 | 版本 | 检查 |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| Node.js（可选） | 18+ | 只有改前端面板才需要 |

**② 拉代码并启动**

```bash
git clone https://github.com/Song-xuan-xuan/wut-deepseek2api.git
cd wut-deepseek2api

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # Windows: .venv\Scripts\pip install -r requirements.txt
cp config.example.json config.json                 # Windows: copy config.example.json config.json
.venv/bin/python -m app                            # Windows: .venv\Scripts\python -m app

```



**③ 一键认证**

打开 <http://127.0.0.1:8787/dashboard> → 左侧「认证」→ 输入**学号** → 点「认证」。

看到"认证成功，token 剩 7.0 天"就好了：学号写回 `config.json`，token 写到 `data/token`，
之后每 60 分钟检查、剩余不足 2 天自动续期——**再也不用管 token**。

### 二、Docker 部署（推荐）

不装 Python，只要 `docker` + `docker compose`：

```bash
git clone https://github.com/Song-xuan-xuan/wut-deepseek2api.git
cd wut-deepseek2api

cp .env.example .env        # 填 WHUT_EMAIL=你的学号（也可以先本地认证一次，凭证落在 ./data/token）
docker compose up -d --build
docker compose logs -f      # 看到 "token 7.0 天后过期" 就成功了
```

验证：`curl --noproxy '*' -s 127.0.0.1:8787/health`，然后打开 <http://127.0.0.1:8787/dashboard>。

数据都在 `./data`（`token` + `usage.db`），删容器不丢。

### 如果开了代理（FlClash / Clash 等

容器流量会跟着宿主机的系统代理走，而学校会拒绝代理节点的出口 IP。
症状很好认：**宿主机 `curl` 返回 200，容器里 TLS 握手却被重置 / `/health` 报 `ConnectError`**。

解决办法：**在代理规则里让学校域名直连**。以 FlClash 为例，四步：

1. 打开 FlClash → 左侧 **配置（Profiles）** → 找到你在用的那份 → 点右侧 **编辑（Edit / 编辑文件）**
2. 找到 `rules:` 这一段，把下面两行**加到最前面**（缩进与 `rules:` 同级，两个空格起头）：

   ```yaml
   rules:
     - DOMAIN-SUFFIX,whut.edu.cn,DIRECT
     - IP-CIDR,218.197.110.99/32,DIRECT,no-resolve
     # ↓ 以下是原本就有的规则，保持不动
     - DOMAIN-SUFFIX,google.com,Proxy
     ...
   ```

3. 保存（`⌘S` / 右下角保存），FlClash 会自动重载配置；没重载就点一下该配置重新应用
4. 验证——这条命令输出 `200` 就通了：

   ```bash
   docker compose exec whut-2api python -c \
     "import urllib.request;print(urllib.request.urlopen('https://chatcloud.whut.edu.cn/api/config',timeout=10).status)"
   ```

   如果还是超时/报错，再补一条（有些订阅会把 IP 直连写死）：

   ```yaml
   - AND,((DOMAIN-SUFFIX,whut.edu.cn),(NETWORK,tcp)),DIRECT

## 接入客户端

所有客户端都是同一组值（面板「仪表盘」页可一键复制，或「分发 / Key」页下载现成模板）：

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8787/v1` |
| API Key | `sk-whut-local`（`config.json` 的 `client_api_key`） |
| 模型 | `deepseek-v4-flash` |

<details>
<summary><b>pi</b>（`~/.pi/agent/models.json`）</summary>

```json
{
  "whut": {
    "baseUrl": "http://127.0.0.1:8787/v1",
    "api": "openai-completions",
    "apiKey": "sk-whut-local",
    "models": [
      {
        "id": "deepseek-v4-flash",
        "name": "WHUT DeepSeek V4 Flash",
        "contextWindow": 700000,
        "maxTokens": 120000,
        "input": ["text", "image"],
        "reasoning": false,
        "compat": {
          "supportsStore": false,
          "supportsDeveloperRole": false,
          "supportsReasoningEffort": false,
          "maxTokensField": "max_tokens",
          "supportsStrictMode": false
        }
      }
    ]
  }
}
```

```bash
pi --model whut/deepseek-v4-flash
```
</details>

<details>
<summary><b>Claude Code</b>（走 `/v1/messages`）</summary>

```bash
# 方式一：临时覆盖，不影响已有的 ~/.claude/settings.json
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
export ANTHROPIC_AUTH_TOKEN="sk-whut-local"
export ANTHROPIC_MODEL="deepseek-v4-flash"
export ANTHROPIC_SMALL_FAST_MODEL="deepseek-v4-flash"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=700000
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

claude -p "只回复两个字：成功"
```

方式二：把「分发 / Key」页生成的 `claude-code` 模板存成 `~/.claude/whut.json`，
然后 `claude --settings ~/.claude/whut.json`（推荐，避免和已有配置冲突）。
</details>

<details>
<summary><b>Codex CLI</b>（走 `/v1/responses`）</summary>

`~/.codex/config.toml`：

```toml
model = "deepseek-v4-flash"
model_provider = "whut"

[model_providers.whut]
name = "WHUT OpenWebUI"
base_url = "http://127.0.0.1:8787/v1"
env_key = "WHUT_API_KEY"
wire_api = "responses"
request_max_retries = 2
stream_max_retries = 1
```

```bash
export WHUT_API_KEY=sk-whut-local
codex
```
</details>

<details>
<summary><b>Cherry Studio / Chatbox</b></summary>

设置 → 模型服务 → 添加 → **OpenAI**，填上表三项，模型名手填 `deepseek-v4-flash`。
</details>

<details>
<summary><b>Cline / Roo Code</b>（VS Code）</summary>

Provider 选 **OpenAI Compatible**，填 Base URL / Key / Model ID，**勾选 "Supports Tools"**。
</details>

<details>
<summary><b>Continue</b>（`~/.continue/config.json`）</summary>

```json
{
  "models": [
    {
      "title": "WHUT DeepSeek",
      "provider": "openai",
      "model": "deepseek-v4-flash",
      "apiBase": "http://127.0.0.1:8787/v1",
      "apiKey": "sk-whut-local"
    }
  ]
}
```
</details>

<details>
<summary><b>命令行 / Python SDK</b></summary>

```bash
# OpenAI 协议
curl --noproxy '*' -s 127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-whut-local" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}],"max_tokens":512}'

# Anthropic 协议
curl --noproxy '*' -s 127.0.0.1:8787/v1/messages \
  -H "x-api-key: sk-whut-local" -H "anthropic-version: 2023-06-01" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","max_tokens":256,"messages":[{"role":"user","content":"你好"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="sk-whut-local")
resp = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "你好"}],
    max_tokens=512,
)
print(resp.choices[0].message.content)
```
</details>

---

## 用量统计

所有请求都会落库到 `data/usage.db`（SQLite），重启不清零，面板可按区间/模型/Key 查看，也能导出 CSV。

![用量统计](https://raw.githubusercontent.com/Song-xuan-xuan/wut-deepseek2api/main/web/public/screenshots/usage.png)

## 模型能力速查

学校三个模型的真实表现（**2026-10-26 实测，真实打到上游，不是抄官方参数**）：

| 模型 | 对话 | 流式 | 首字 TTFT | 生成速度 | 工具调用 | 上下文 | 图片 | 并发 | 定位 |
|---|---|---|---|---|---|---|---|---|---|
| **`deepseek-v4-flash`** | ✅ | ✅ | **≈ 2.5 s** | **≈ 27 tok/s** | ✅ | **≥ 745,080 tokens** | ❌ | 1/2/4/6 全部零失败 | **主力** |
| `deepseek-v3` | ✅ | ✅ | ≈ 7.4 s（慢 3 倍） | ≈ 15 tok/s | ⚠️ `auto` 可以，`required` 失效 | ≥ 37,535 tokens（1 MiB 内封顶） | ❌ | 1/2/4/6 全部零失败 | 备用（慢但稳） |
| `qwen3-235b-a22b` | ❌ | — | — | — | — | — | — | — | 上游 `429`，**不可用** |

**更详细的测评请看 👉 [docs/model-capabilities.md](docs/model-capabilities.md)**


### 三条实测得出的硬约束

| 约束 | 实测值 | 对你的影响 |
|---|---|---|
| **请求体上限** | `1,048,576` 字节（1 MiB），超了 nginx 直接 `413` | 一次能塞多少字由体量决定，折合 token 看密度：中文 ≈ 19.7 万 / 英文散文 ≈ 21 万 / 高熵 ASCII·密集代码 ≈ 78 万（实测见 docs/model-capabilities.md） |
| **输出预算** | 简单问答 ≥ 512，多轮与 agent 单步 **≥ 1024**，工具/推理 **≥ 2048** | `flash` 的思维链占输出 token 的 **68%**，给少了会得到「200 + 空正文」，看着像模型坏了 |
| **`tool_choice`** | 一律用 **`auto`** | `required` 不可靠：上游把工具调用写成 `content` 里的 `<｜DSML｜invoke …>` 标记，`tool_calls` 却是 `null` |
## 配置（`config.json`）

首次启动自动生成；用面板认证过的不用改。

| 字段 | 默认 | 说明 |
|---|---|---|
| `listen.host` / `port` | `127.0.0.1:8787` | 只本机；改 `0.0.0.0` 才能局域网分发 |
| `upstream.base_url` | 学校地址 | 支持子路径部署 |
| `upstream.token_file` | `data/token` | 按 mtime 热加载，换 token 不用重启 |
| `upstream.retries` | `2` | 429/502-504/网络错误重试 |
| `upstream.disabled_models` | `[]` | 永不列出的模型（学校侧挂了的模型可以写进来） |
| `upstream.circuit_breaker_threshold` / `_seconds` | `3` / `60` | 连续不可达 → 熔断冷却 |
| `client_api_key` | `sk-whut-local` | 主 key；置空且无子 key 时不校验 |
| `max_tokens_cap` | `120000` | 输出上限钳制（实测 131072 通过，略留余量） |
| `strip_fields` | `store,metadata,…` | 上游不一定认识的字段 |
| `keep_alive_ms` | `8000` | 流式静默心跳，0=关闭 |
| `default_model` | `deepseek-v4-flash` | 请求没带 model 的兜底 |
| 模型窗口 | `deepseek-v4-flash`：700K 上下文 / 120K 输出 | 来自 [实测报告](docs/model-capabilities.md)：高熵文本下输入 745,080 tokens 仍能召回开头内容，输出 131072 通过 |
| ⚠️ 请求体硬限制 | **1 MiB** | 每次请求都要把「系统提示 + 全部历史 + 工具定义」整包发出去，字节超 1 MiB 会被学校反代 413。**中文约 20 万 tokens、英文散文也约 20 万**（只有高熵代码类文本能接近 78 万），所以长会话实际可用输入 ≈ 20 万 tokens，而不是 700K |
| `repo_url` | 本仓库地址 | 面板右上「GitHub」图标跳转的地址 |
| `token_refresh.email` | 学号 | 面板认证后自动写入 |
| `token_refresh.refresh_before_days` | `2` | 剩余小于该值就续期 |
| `token_refresh.check_interval_minutes` | `60` | 后台检查间隔 |
| `usage.db_path` | `data/usage.db` | 用量库（含 key 与认证事件） |
| `usage.retention_days` | `180` | 自动清理旧记录 |
| `access.default_rpm_limit` | `120` | 新 key 默认每分钟上限（0=不限） |
| `access.default_daily_token_limit` | `0` | 新 key 默认每日 token 配额（0=不限） |

环境变量覆盖：`WHUT_BASE_URL`、`WHUT_TOKEN`、`WHUT_TOKEN_FILE`、`WHUT_EMAIL`、`WHUT_PORT`、`WHUT_HOST`、`WHUT_CLIENT_KEY`、`WHUT_MAX_TOKENS_CAP`、`WHUT_DATA_DIR`。

---

## 目录

```
wut-deepseek2api/
├── app/                      后端（FastAPI）
│   ├── core/                 配置 · SQLite · 路径 · 日志
│   ├── providers/            上游抽象 + OpenWebUI 实现
│   ├── modules/
│   │   ├── proxy/            /v1/chat/completions · 参数修正 · SSE 透传
│   │   ├── anthropic/        /v1/messages（Claude Code）
│   │   ├── responses/        /v1/responses（Codex CLI）
│   │   ├── auth/             学号登录 · 自动续期 · 事件历史
│   │   ├── usage/            用量聚合 · CSV
│   │   ├── access/           多 key · 配额 · 限速 · 模板
│   │   └── system/           健康检查 · 静态资源
│   └── main.py               组装 + 挂模块
├── web/                      面板（Vue 3 + Vite）
│   ├── src/                  源码
│   ├── public/               架构动画图 · 截图
│   └── dist/                 构建产物（已提交，运行不需要 Node）
├── scripts/modeltest/        模型实测（bench · suites · report）+ results/
├── docs/                     说明文档（Docker 部署手册、模型与协议实测报告）
├── data/                     运行时数据（token、用量库，已忽略）
├── Dockerfile · docker-compose.yml · .env.example
├── requirements.txt · config.example.json
└── LICENSE
```

## 免责声明

- 这是一个**个人学习项目**，用于学习 HTTP 代理、流式传输与协议转换。
- 与**武汉理工大学官方无关**，不代表学校立场，也未获得学校授权。
- 使用时请遵守学校的网络与信息系统管理规定，仅限个人本地使用，不要对外提供服务。

MIT License · 详见 [LICENSE](./LICENSE)
