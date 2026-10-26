# Docker 部署手册

> 适用：想用容器跑、不想在本机装 Python 环境的同学。
> 全程只需要 `docker` 和 `docker compose`，所有运行数据都落在项目下的 `./data`。

---

## 0. 装 Docker

| 系统 | 怎么装 |
|---|---|
| macOS | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/)（Apple 芯片选 Apple Silicon） |
| Windows 10/11 | [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)，安装时选 WSL2 后端 |
| Linux | `curl -fsSL https://get.docker.com | sh`，再把当前用户加进 docker 组：`sudo usermod -aG docker $USER`（重新登录生效） |

装完确认：

```bash
docker --version           # 期望 Docker version 24 以上
docker compose version     # 期望 Docker Compose version v2 以上
docker run --rm hello-world
```

---

## 1. 拿代码

```bash
git clone https://github.com/Song-xuan-xuan/wut-deepseek2api.git
cd wut-deepseek2api
```

---

## 2. 准备凭证（三选一）

容器只会读 `./data` 目录，凭证也放这里。

### 方式 A：先本地跑一次、在面板认证（最省事）

如果本机有 Python：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app          # 打开 http://127.0.0.1:8787/dashboard → 认证页填学号
# 认证成功后 Ctrl+C 停掉
```

之后 `./data/token` 就存在了，Docker 直接复用，**不用再认证**。

### 方式 B：写进 `.env`（只给学号，自动换 token）

```bash
cp .env.example .env
# 编辑 .env，把 WHUT_EMAIL 改成你的学号
```

```dotenv
WHUT_EMAIL=1023005562
```

> 学校这套 OpenWebUI 的 `signin` 只校验学号存在、**不校验密码**，所以给学号就能换到 token，7 天有效、到期自动续。

### 方式 C：直接给 JWT

```bash
mkdir -p data
pbpaste > data/token            # macOS：先复制浏览器里的 JWT
# Linux: xclip -o > data/token    Windows: 手动粘到 data/token
chmod 600 data/token
```

JWT 从哪来：浏览器登录 <https://chatcloud.whut.edu.cn> → DevTools → **Application → Local Storage → 键 `token`**。

---

## 3. 启动

```bash
docker compose up -d --build
```

第一次会拉 `python:3.12-slim` 并装依赖，大约 1~3 分钟；之后重建只要几秒（有构建缓存）。

看状态：

```bash
docker compose ps          # STATUS 里出现 (healthy) 就正常
docker compose logs -f     # 实时日志，Ctrl+C 退出查看（不会停服务）
```

启动日志应该是这样：

```
whut-2api  →  http://0.0.0.0:8787/v1
  面板     http://0.0.0.0:8787/dashboard
  上游     https://chatcloud.whut.edu.cn
  凭证     data/token
  用量库   data/usage.db
  本地 key sk-whut-local
  学号     1023005562（自动续期开）
  token    7.0 天后过期
```

---

## 4. 验证

```bash
# 健不健康（三行都是 true 才算通）
curl --noproxy '*' -s 127.0.0.1:8787/health | python3 -m json.tool

# 对话（PowerShell 用 curl.exe，并把 --noproxy 去掉）
curl --noproxy '*' -s 127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-whut-local" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

浏览器打开面板：<http://127.0.0.1:8787/dashboard>
（概览 / 用量统计 / 认证 / 分发·Key / 系统信息）

---

## 5. 接入客户端

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8787/v1` |
| API Key | `sk-whut-local` |
| Model | `deepseek-v4-flash` |

Claude Code 用 `http://127.0.0.1:8787` + `ANTHROPIC_BASE_URL`，Codex CLI 用 `.../v1` + `OPENAI_BASE_URL`，具体模板在面板「分发 / Key」里一键复制。

---

## 6. 日常运维

```bash
docker compose logs -f --tail=100   # 看日志
docker compose restart              # 重启
docker compose stop                 # 停止（保留容器和数据）
docker compose down                 # 停止并删除容器（./data 保留）
docker compose up -d --build        # 拉最新代码后重建
docker compose pull                 # 直接用 ghcr 上的镜像（更快）
```

换 token / 换学号：改 `.env` 或 `./data/token` 后 `docker compose restart`。

数据在哪：

```
data/
├── token       学校会话 token（600）
└── usage.db    用量统计（含分发的 key，600）
```

删容器不会删数据；**别 `rm -rf data`**，否则要重新认证。

---

## 7. 常见问题

### ① 容器启动失败：`PermissionError: '/app/data/...'`

Linux 上宿主机目录属主不对。v0.6 起入口脚本会自动 `chown`，如果还报错：

```bash
sudo chown -R 10001:10001 data
docker compose restart
```

### ② `/health` 返回 503、日志里 `ConnectError` / `SSLEOFError`

**容器连不上学校**。典型症状：宿主机 `curl` 是 200，容器里 TCP 能连上、TLS 握手却被重置。

已定位到两类原因，都是「容器流量被本机代理接管」：

| 宿主环境 | 表现 | 原因 |
|---|---|---|
| macOS + **OrbStack** | 容器出口 IP = 代理节点 IP（宿主机直连是另一个 IP） | OrbStack 默认让**所有容器跟随 macOS 系统代理**（`network_proxy=auto`） |
| macOS + Docker Desktop + Clash TUN | 同上 | TUN 接管整个网络栈 |

学校边缘会拒绝代理节点的出口 IP，所以 TLS 被 RST。**三种解法，按推荐顺序：**

#### 方案 A（推荐）：代理软件里让学校域名直连

FlClash / Clash Verge / Surge 等都一样，加两条规则（放最前面）：

```yaml
rules:
  - DOMAIN-SUFFIX,whut.edu.cn,DIRECT
  - IP-CIDR,218.197.110.99/32,DIRECT,no-resolve
```

保存并重载配置，然后验证：

```bash
docker compose exec whut-2api python -c \
  "import urllib.request;print(urllib.request.urlopen('https://chatcloud.whut.edu.cn/api/config',timeout=10).status)"
# 期望：200
```

好处：只影响这个域名，其他容器 / 拉镜像照常走代理。

#### 方案 B：让 OrbStack 不代理容器（临时方案）

```bash
orb config set network_proxy none      # 或 OrbStack 设置 → Network → Proxy → None
docker compose restart
```

实测这样容器出口就变回直连、学校可达、对话正常。**但副作用：`docker pull` 会失败**
（`failed to resolve reference ... Bad Gateway`），需要拉镜像时再切回来：

```bash
orb config set network_proxy auto      # 恢复默认
```

#### 方案 C：换一台没有代理的机器

校内 Linux 服务器 / 云主机上直接 `docker compose up -d --build`，完全没有这个问题。

> 纯内网环境（校园网、无代理）三种折腾都不需要。

### ③ 端口 8787 被占用

```bash
lsof -ti:8787 | xargs kill      # macOS / Linux
```

或改 `docker-compose.yml` 的端口映射，例如 `127.0.0.1:8790:8787`，访问时用 8790。

### ④ 想让手机 / 平板也能用

两处一起改：

```yaml
ports:
  - "8787:8787"          # 去掉 127.0.0.1 前缀
```

然后把 API Key 换成随机串，避免别人猜：

```dotenv
WHUT_CLIENT_KEY=sk-换成你自己的长随机串
```

> 同时确认防火墙放行 8787、且设备在同一局域网。

### ⑤ 想用官方预构建镜像（不本地构建）

```bash
docker compose pull && docker compose up -d
```

镜像：`ghcr.io/song-xuan-xuan/wut-deepseek2api:latest`（amd64 + arm64）。

---

## 8. 和本地源码运行的区别

| | Docker | 本地源码 |
|---|---|---|
| 依赖 | 不用装 Python | 需要 Python 3.10+ |
| 数据位置 | `./data`（挂载） | `./data`（同一目录） |
| 改代码生效 | 需要 `--build` | 重启进程即可 |
| 改面板 | 需要 `npm run build` 后再 build 镜像 | 同上 |

两者可以随时切换，因为它们读写的是同一个 `./data`，token 和统计都不丢。
