# whut-2api

把武汉理工的 `chatcloud.whut.edu.cn`（学校 OpenWebUI · "DeepSeek云服务版"）包装成**标准协议接口**，让 pi、Claude Code、Codex CLI、Cline、Cherry Studio 或任何 OpenAI SDK 都能直接用上学校的模型。

[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](.) [![Python](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](.) [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)

---

## 原理

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Song-xuan-xuan/wut-deepseek2api/main/docs/architecture-dark.svg">
  <img src="https://raw.githubusercontent.com/Song-xuan-xuan/wut-deepseek2api/main/docs/architecture-light.svg" alt="whut-2api 工作原理：AI 客户端 → 本地代理（注入 token / 续期 / 统计 / 分发）→ 学校 OpenWebUI → DeepSeek" width="100%">
</picture>

### 为什么需要这一层

学校这套 OpenWebUI **本来就提供 OpenAI 格式的接口**（`POST /api/chat/completions`），所以不需要逆向网页、不需要浏览器自动化。
真正麻烦的只有两个：

1. **个人 API Key 被管理员关了**（`enable_api_key: false`）→ 只能用浏览器登录后那份 JWT；
2. 这份 JWT **7 天过期**，而且是一大串 `eyJhbGciOi...`——直接塞进各个 AI 工具，每 7 天要改一遍配置，还没法统一做参数修正。

于是有了这层本地代理：**把「凭证」和「协议修正」从每个客户端里收出来，集中在一处处理。**

### 一次请求发生了什么

| 步骤 | 发生了什么 |
|---|---|
| ① | 客户端用**本地假 key**（`sk-whut-local`）请求 `http://127.0.0.1:8787/v1/...` |
| ② | 代理**注入真实的学校 JWT**（从 `~/.whut-jwt` 读，按修改时间热加载，换 token 不用重启） |
| ③ | 代理**修正参数**：`max_completion_tokens → max_tokens`、钳制输出上限、剔除 `store`/`metadata` 等上游不认识的字段 |
| ④ | 转发到 `https://chatcloud.whut.edu.cn/api/chat/completions`，学校再转给 DeepSeek |
| ⑤ | 响应**字节级透传**回来（流式不拆包、不重组），同时把 usage / 耗时**落库**到 SQLite |
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

### 几个容易被忽略的工程细节

| 细节 | 为什么重要 |
|---|---|
| **上游缺 `[DONE]` 时补一个** | 否则 agent 会一直挂着等流结束 |
| **静默 8 秒发 SSE 心跳** | 思考模型首字要 2~6 秒，中间网关会掐断长连接 |
| **连续 3 次连不上就熔断 60 秒** | 学校侧偶发不可达时，反复重试只会把封禁拖得更久 |
| **429 尊重 `Retry-After`** | 上游限流时不要盲目退避 |
| **`trust_env=False`** | Python 的 httpx 默认读 `HTTP_PROXY`/`ALL_PROXY`，会把校园地址导去本机代理，而学校边缘会拒绝代理出口 IP |

### 为什么续期不需要密码

实测（2026-11）：学校的 `POST /api/v1/auths/signin` 只校验**学号是否存在**，`password` 字段只要非空即可通过。
所以本项目只需要学号，从不保存也不需要你的密码。这是学校侧的认证缺陷，建议顺手告知管理员。

---

## 安装教程

> **共同前提：必须能访问校园网**（校内 / 学校 VPN）。校外直连会超时。
> 若本机开着 Clash / Surge 等代理，请把 `whut.edu.cn` 加进**直连规则**——学校会拒绝代理节点的出口 IP。

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

./start.sh          # macOS / Linux：自动建 venv、装依赖、生成 config.json、启动
# Windows：双击 start.bat
```

手动等价版：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json && chmod 600 config.json
.venv/bin/python -m app
```

**③ 一键认证**

打开 <http://127.0.0.1:8787/dashboard> → 左侧「认证」→ 输入**学号** → 点「认证」。

看到"认证成功，token 剩 7.0 天"就好了：学号写回 `config.json`，token 写到 `~/.whut-jwt`，
之后每 60 分钟检查、剩余不足 2 天自动续期——**再也不用管 token**。

**④ 验证**

```bash
curl --noproxy '*' -s 127.0.0.1:8787/health | python3 -m json.tool
# 三个 checks 都是 true，models 里有 deepseek-v4-flash 即可
```

> **改面板**（可选）：`cd web && npm install && npm run dev` → <http://127.0.0.1:5173>，
> 接口自动代理到 8787。改完 `npm run build`，产物在 `web/dist`（已随仓库提交，普通用户不需要 Node）。

### 二、Docker 部署（推荐）

适合：日常使用。不用装 Python、不污染系统、`restart: unless-stopped` 自动重启。

**① 准备环境**

```bash
docker --version           # Docker Desktop 或 Docker Engine
docker compose version     # Compose v2
```

**② 准备凭证（二选一）**

- **推荐**：先用「本地开发」那行命令跑一次、在面板里认证好——容器挂载同一个数据目录，无需重复认证；
- **或者**直接用学号让容器自己换：

  ```bash
  cp .env.example .env
  # 编辑 .env：WHUT_EMAIL=<你的学号>
  ```

**③ 启动**

```bash
docker compose up -d --build
docker compose logs -f          # 看到 "whut-2api → http://0.0.0.0:8787/v1" 即成功
```

不用 compose 也可以：

```bash
docker build -t whut-2api .
docker run -d --name whut-2api \
  -p 127.0.0.1:8787:8787 \
  -e WHUT_EMAIL=<你的学号> \
  -v "$HOME/.whut-2api:/home/app/.whut-2api" \
  --restart unless-stopped \
  whut-2api
```

**④ 验证**

```bash
curl --noproxy '*' -s 127.0.0.1:8787/health | python3 -m json.tool
# 然后浏览器打开 http://127.0.0.1:8787/dashboard
```

**⑤ 运维**

```bash
docker compose restart          # 改完配置
docker compose down             # 停止
docker compose up -d --build    # 更新代码后重建
docker compose logs -f          # 看日志
```

数据（token + 用量库）都在宿主机的 `./data` 目录，**删容器不丢**。

> **⚠️ macOS + Clash/Surge TUN 模式的坑**：容器流量会走代理节点，学校会直接 RST（实测）。
> 要么在代理软件里给 `whut.edu.cn` 加 `DIRECT` 规则，要么改用本地开发方式。
> 部署到没有代理的 Linux 服务器则完全没这个问题。

---

## 接入客户端

所有客户端都是同一组值（面板「仪表盘」页可一键复制，或「分发 / Key」页下载现成模板）：

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8787/v1` |
| API Key | `sk-whut-local`（`config.json` 的 `client_api_key`） |
| 模型 | `deepseek-v4-flash`（或 `deepseek-v3`） |

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
        "contextWindow": 131072,
        "maxTokens": 32768,
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
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=131072
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

## 面板

<http://127.0.0.1:8787/dashboard> —— Vue 3 + Vite，支持明暗主题（浅色 / 深色 / 跟随系统）。

| 页面 | 内容 |
|---|---|
| **仪表盘** | 请求数 / token / 错误 / token 剩余 / 模型数 / 续期状态，24h 面积图，客户端配置一键复制 |
| **用量统计** | 今天·24h·7天·30天·全部，输入输出与思维链 token，p50/p95 耗时，按模型 / 按 Key 拆分，CSV 导出 |
| **认证** | 一键认证（学号）、token 指纹、自动续期状态、手动粘贴、认证历史、失败退避 |
| **分发 / Key** | 多 key 管理（开关 / 换 key / 删除）、每分钟限速、每日 token 配额、六种客户端模板、局域网地址 |
| **系统** | 版本 / 监听 / 上游 / 配置路径 / 数据库、熔断状态、常用命令 |

「分发 / Key」提供的现成模板：`pi` · `codex` · `claude-code` · `continue` · `curl` · `python`，可直接复制或下载。

---

## 配置（`config.json`）

首次启动自动生成；用面板认证过的同学基本不用改。

| 字段 | 默认 | 说明 |
|---|---|---|
| `listen.host` / `port` | `127.0.0.1:8787` | 只本机；改 `0.0.0.0` 才能局域网分发 |
| `upstream.base_url` | 学校地址 | 支持子路径部署 |
| `upstream.token_file` | `~/.whut-jwt` | 按 mtime 热加载，换 token 不用重启 |
| `upstream.retries` | `2` | 429/502-504/网络错误重试 |
| `upstream.circuit_breaker_threshold` / `_seconds` | `3` / `60` | 连续不可达 → 熔断冷却 |
| `client_api_key` | `sk-whut-local` | 主 key；置空且无子 key 时不校验 |
| `max_tokens_cap` | `32768` | 输出上限钳制 |
| `strip_fields` | `store,metadata,…` | 上游不一定认识的字段 |
| `keep_alive_ms` | `8000` | 流式静默心跳，0=关闭 |
| `default_model` | `deepseek-v4-flash` | 请求没带 model 的兜底 |
| `token_refresh.email` | 学号 | 面板认证后自动写入 |
| `token_refresh.refresh_before_days` | `2` | 剩余小于该值就续期 |
| `token_refresh.check_interval_minutes` | `60` | 后台检查间隔 |
| `usage.db_path` | `~/.whut-2api/usage.db` | 用量库（含 key 与认证事件） |
| `usage.retention_days` | `180` | 自动清理旧记录 |
| `access.default_rpm_limit` | `120` | 新 key 默认每分钟上限（0=不限） |
| `access.default_daily_token_limit` | `0` | 新 key 默认每日 token 配额（0=不限） |

环境变量覆盖：`WHUT_BASE_URL`、`WHUT_TOKEN`、`WHUT_TOKEN_FILE`、`WHUT_EMAIL`、`WHUT_PORT`、`WHUT_HOST`、`WHUT_CLIENT_KEY`、`WHUT_MAX_TOKENS_CAP`、`WHUT_DATA_DIR`。

---

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 面板打不开 | 服务没起 / 端口改了 | 先访问 `/health` |
| 认证报「审核尚未通过」 | 学校还没批你的申请 | 找管理员 |
| `403 Not authenticated` | token 过期 | 面板点「立即续期」或重新认证 |
| `400 Invalid max_tokens` | 输出上限太大 | 调小 `max_tokens_cap` |
| 正文为空、只有思考 | 思维链吃光预算 | `max_tokens_cap` ≥ 8192 |
| 面板显示「熔断冷却」 | 连续 3 次连不上，程序主动停手 | 等 60 秒自动恢复，别反复刷 |
| 学校整站连不上 | 临时封禁 / 维护 | 等 30~60 分钟 |
| `curl` 连不上但浏览器能开 | shell 里有 `http_proxy` | `curl` 加 `--noproxy '*'` |
| Docker 里连不上学校 | 容器流量走了代理 | 加直连规则，或改用本地方式 |
| 子 key 报 429「配额已用尽」 | 当天 token 超了 | 次日恢复，或在分发页调大配额 |
| Claude Code 连不上 | Base URL 少了/多了 `/v1` | 填 `http://127.0.0.1:8787`（不含 `/v1`） |
| Codex 报 unsupported API | 没走 responses 协议 | `wire_api = "responses"` |
| Windows 上 `python` 不是命令 | 没加 PATH | 用 `py -3` |

日志：前台直接看；后台 `docker compose logs -f`，或 `nohup` 的重定向文件。

---

## 目录

```
wut-deepseek2api/
├── app/                      后端（FastAPI）
│   ├── core/                 配置 · SQLite · 日志 · 路径
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
│   └── dist/                 构建产物（已提交，运行不需要 Node）
├── docs/                     架构动画图（浅色 / 深色）
├── Dockerfile · docker-compose.yml · .env.example
├── requirements.txt · config.example.json
├── start.sh · start.bat      本机一键启动
└── LICENSE
```

---

## 免责声明

- 本项目**只是复用你自己账号**的会话凭证，调用学校页面的同一个接口，属于个人自用。
- 请勿把服务暴露到公网、勿分享 token、勿代跑或批量抓数据；**收到学校通知请立即停止**。
- 学校关闭了个人 API Key 说明对程序化访问有顾虑，本方案处于灰色地带，**风险自担**。
- 与武汉理工大学官方无关。

MIT License · 详见 [LICENSE](./LICENSE)
