#!/usr/bin/env bash
# ============ 统一管理脚本（Git Bash / Linux / macOS，健壮版） ============
# 用法: bash scripts/manage.sh <命令>
#   setup          安装依赖（创建虚拟环境）
#   run-desktop    运行桌面版（启动后端 + 打开网页）
#   run-weapp      运行小程序版（启动后端 + 打印导入指引）
#   build-desktop  构建桌面版静态产物 → dist/desktop
#   build-weapp    校验小程序源码 → dist/weapp（供微信开发者工具上传）
#
# 设计要点：启动前做预检（python/venv/依赖）并打印状态；端口占用给出警告；
#          健康检查通过后再开浏览器；run-* 通过 trap 在退出时一并停止后端。
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
VENV="$ROOT/.venv"

PY="$VENV/Scripts/python.exe"
[ -x "$VENV/bin/python" ] && PY="$VENV/bin/python"
UVICORN="$VENV/Scripts/uvicorn.exe"
[ -x "$VENV/bin/uvicorn" ] && UVICORN="$VENV/bin/uvicorn"
PORT=$(grep -A3 '^server:' config.yaml | grep 'port:' | head -1 | sed 's/[^0-9]//g')
PORT=${PORT:-8600}
URL="http://localhost:$PORT"

say() { echo -e "\033[36m▶ $1\033[0m"; }
ok()  { echo -e "\033[32m✓ $1\033[0m"; }
warn(){ echo -e "\033[33m! $1\033[0m"; }
err() { echo -e "\033[31m✗ $1\033[0m"; }

# 端口是否被占用
check_port() {
  if command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      warn "端口 $PORT 已被占用，后端可能无法绑定（可结束占用进程或改 config.yaml 的 server.port）"
    fi
  elif command -v netstat >/dev/null 2>&1; then
    if netstat -ano 2>/dev/null | grep -q ":${PORT} "; then
      warn "端口 $PORT 已被占用，后端可能无法绑定（可结束占用进程或改 config.yaml 的 server.port）"
    fi
  fi
}

# 预检：确保 venv 与依赖就绪
preflight() {
  say "检查运行环境..."
  if [ ! -x "$PY" ]; then
    say "未检测到虚拟环境，正在创建 .venv ..."
    python -m venv "$VENV" 2>/dev/null || py -3 -m venv "$VENV" || {
      err "创建虚拟环境失败：请确认已安装 Python 3.10+ 并在 PATH 中。"
      exit 1
    }
  fi
  if ! "$PY" -c "import fastapi,uvicorn" >/dev/null 2>&1; then
    say "检测到依赖缺失，正在安装后端依赖（首次可能较慢）..."
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r backend/requirements.txt
  fi
  ok "运行环境就绪: $PY"
}

# 健康检查（curl 缺失则回退 python）
health_ok() {
  if curl -s "$URL/api/health" >/dev/null 2>&1; then return 0; fi
  if "$PY" -c "import urllib.request,sys; urllib.request.urlopen('$URL/api/health', timeout=1); print('ok')" >/dev/null 2>&1; then return 0; fi
  return 1
}

# 停止后端（按端口）
kill_backend() {
  if command -v lsof >/dev/null 2>&1; then
    pids=$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
  else
    pids=$(netstat -ano 2>/dev/null | grep ":${PORT} " | awk '{print $5}' | sort -u || true)
  fi
  for p in $pids; do kill "$p" 2>/dev/null || true; done
}

start_backend() {
  if health_ok; then
    ok "后端已在运行: $URL"; return
  fi
  check_port
  if [ ! -f "$UVICORN" ]; then
    say "未找到 uvicorn，先执行依赖安装…"; do_setup
  fi
  say "启动后端: $URL"
  (cd backend && "$UVICORN" app.main:app --host 0.0.0.0 --port "$PORT" >/tmp/medical-agent.log 2>&1 &)
  for i in $(seq 1 60); do
    health_ok && break
    sleep 0.5
  done
  if health_ok; then
    ok "后端已就绪: $URL"
  else
    err "后端启动失败，查看 /tmp/medical-agent.log"
    exit 1
  fi
}

do_setup() {
  say "创建虚拟环境并安装依赖…"
  python -m venv "$VENV" 2>/dev/null || py -3 -m venv "$VENV"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r backend/requirements.txt
  ok "依赖安装完成（接下来运行: bash scripts/manage.sh run-desktop）"
}

do_run_desktop() {
  preflight
  start_backend
  say "打开桌面版网页…"
  (start "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null || open "$URL" 2>/dev/null || echo "  请手动访问 $URL") &
  echo "  ✅ 桌面版已启动（钉钉式网页布局）。按 Ctrl+C 停止服务。"
  trap 'kill_backend; echo -e "\n已停止后端。"' EXIT INT TERM
  wait
}

do_run_weapp() {
  preflight
  start_backend
  cat <<EOF

  ✅ 后端已启动: $URL

  ▶ 小程序版运行步骤：
  1. 打开「微信开发者工具」→ 导入项目 → 选择目录:
       $ROOT/weapp
     AppID 可先用测试号（已默认 touristappid）
  2. 详情 → 本地设置 → 勾选「不校验合法域名…」
  3. 编译预览即可（默认请求 $URL）
  4. 真机预览时，请把 weapp/utils/api.js 中的
     BASE_URL 改为你电脑的局域网 IP，如 http://192.168.x.x:$PORT
EOF
  echo "  按 Ctrl+C 停止服务。"
  trap 'kill_backend; echo -e "\n已停止后端。"' EXIT INT TERM
  wait
}

do_build_desktop() {
  say "构建桌面版静态产物…"
  mkdir -p dist/desktop
  cp -r frontend/desktop/* dist/desktop/
  ok "产物: dist/desktop（纯静态，可部署至任意 Web 服务器/Nginx）"
  echo "     部署时请将 /api 反向代理到后端 $PORT 端口"
}

do_build_weapp() {
  say "校验小程序源码…"
  for f in app.js app.json app.wxss project.config.json sitemap.json; do
    [ -f "weapp/$f" ] || { err "缺少 weapp/$f"; exit 1; }
  done
  for p in index chat kb medicine me; do
    for ext in js json wxml wxss; do
      [ -f "weapp/pages/$p/$p.$ext" ] || { err "缺少 weapp/pages/$p/$p.$ext"; exit 1; }
    done
  done
  mkdir -p dist
  rm -rf dist/weapp && cp -r weapp dist/weapp
  ok "小程序源码校验通过，产物: dist/weapp"
  echo "     上传发布：用微信开发者工具打开 weapp/ → 上传 → 提交审核"
  echo "     （CI 自动上传可用 miniprogram-ci，需小程序上传密钥）"
}

case "${1:-}" in
  setup)         do_setup ;;
  run-desktop)   do_run_desktop ;;
  run-weapp)     do_run_weapp ;;
  build-desktop) do_build_desktop ;;
  build-weapp)   do_build_weapp ;;
  *) cat <<EOF
医学问询智能体 · 统一管理脚本
用法: bash scripts/manage.sh <命令>
  setup          安装依赖（创建虚拟环境）
  run-desktop    运行桌面版（后端 + 钉钉式网页）
  run-weapp      运行小程序版（后端 + 导入指引）
  build-desktop  构建桌面版静态产物
  build-weapp    校验并打包小程序源码
EOF
  ;;
esac
