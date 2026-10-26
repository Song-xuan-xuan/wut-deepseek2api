#!/usr/bin/env bash
# macOS / Linux 一键启动：缺虚拟环境就创建，缺依赖就安装，然后启动服务。
set -e

cd "$(dirname "$0")"

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
  echo "❌ 没找到 Python 3.10+，请先安装：https://www.python.org/downloads/"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ 创建虚拟环境（$PY）"
  "$PY" -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, httpx, uvicorn" >/dev/null 2>&1; then
  echo "→ 安装依赖"
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
fi

if [ ! -f config.json ]; then
  cp config.example.json config.json
  chmod 600 config.json
fi

if [ ! -f "$HOME/.whut-jwt" ]; then
  echo "⚠️  还没有 ~/.whut-jwt —— 启动后打开面板，用学号一键认证即可（不需要密码）"
fi

echo "→ 面板 http://127.0.0.1:8787/dashboard   （首次用：左侧「认证」填学号）"
exec .venv/bin/python -m app
