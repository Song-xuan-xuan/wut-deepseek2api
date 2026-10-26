@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [X] 没有找到 py 启动器，请先安装 Python 3.10+ https://www.python.org/downloads/
  echo     安装时务必勾选 "Add python.exe to PATH"
  pause
  exit /b 1
)

if not exist .venv (
  echo -^> 创建虚拟环境
  py -3 -m venv .venv
  if errorlevel 1 (
    echo [X] 创建虚拟环境失败
    pause
    exit /b 1
  )
)

.venv\Scripts\python -c "import fastapi, httpx, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo -^> 安装依赖（第一次会慢一点）
  .venv\Scripts\python -m pip install -q --upgrade pip
  .venv\Scripts\python -m pip install -q -r requirements.txt
)

if not exist config.json (
  copy config.example.json config.json >nul
)

if not exist "%USERPROFILE%\.whut-jwt" (
  echo [!] 还没有 token —— 启动后打开面板，用学号一键认证即可（不需要密码）
)

echo -^> 面板 http://127.0.0.1:8787/dashboard    ^(首次用：左侧「认证」填学号^)
.venv\Scripts\python -m app
pause
