#!/usr/bin/env bash
# KnowledgeOS 本地预览：安装依赖，启动后端 API（8787）与 Studio（5173），打开浏览器。
# 用法：bash scripts/preview.sh        （Ctrl+C 停止两个服务）
# 适用于 macOS / Linux。需要 Python ≥ 3.10 与 Node.js ≥ 22.13；缺少时给出安装提示。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUDIO="$ROOT/prototype/ontology-studio"
LOGS="$ROOT/runtime/preview-logs"
API_PORT=8787
STUDIO_PORT=5173
mkdir -p "$LOGS"

say() { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- Python ≥ 3.10 -----------------------------------------------------------
say "检查 Python（需要 3.10 及以上）"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"; break
  fi
done
if [ -z "$PYTHON" ] && command -v uv >/dev/null 2>&1; then
  say "使用 uv 准备 Python 3.12"
  uv python install 3.12 >/dev/null
  PYTHON="$(uv python find 3.12)"
fi
[ -n "$PYTHON" ] || fail "没有找到 Python 3.10+。macOS 可运行：brew install python@3.12（或安装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh），然后重新运行本脚本。"
echo "使用 $("$PYTHON" --version) ($PYTHON)"

# --- Node.js ≥ 22.13 ----------------------------------------------------------
say "检查 Node.js（需要 22.13 及以上）"
command -v node >/dev/null 2>&1 || fail "没有找到 Node.js。macOS 可运行：brew install node@22（或从 https://nodejs.org 安装 LTS 版），然后重新运行本脚本。"
node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||a===22&&b>=13?0:1)' \
  || fail "Node.js 版本为 $(node --version)，需要 22.13 及以上。macOS 可运行：brew install node@22"
echo "使用 Node.js $(node --version)"
PNPM=(npx --yes pnpm@11.25.0)
if command -v pnpm >/dev/null 2>&1 && [ "$(pnpm --version 2>/dev/null)" = "11.25.0" ]; then PNPM=(pnpm); fi

# --- Ports --------------------------------------------------------------------
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for port in $API_PORT $STUDIO_PORT; do
  if port_busy "$port"; then fail "端口 $port 已被占用。请先停止占用它的程序（lsof -i :$port 可查看），或关闭之前启动的预览。"; fi
done

# --- Backend ------------------------------------------------------------------
say "安装后端依赖（首次约 1 分钟）"
if [ ! -x "$ROOT/.venv/bin/python" ]; then "$PYTHON" -m venv "$ROOT/.venv"; fi
"$ROOT/.venv/bin/python" -m pip install -q --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -q -e "$ROOT[test]"
KOS="$ROOT/.venv/bin/knowledgeos"

say "编译知识库"
"$KOS" --root "$ROOT" rebuild > "$LOGS/rebuild.log" 2>&1 || { tail -n 30 "$LOGS/rebuild.log"; fail "编译失败，详见 $LOGS/rebuild.log"; }

say "启动后端 API：http://127.0.0.1:$API_PORT"
"$KOS" --root "$ROOT" dev-serve --port "$API_PORT" > "$LOGS/api.log" 2>&1 &
API_PID=$!

# --- Studio -------------------------------------------------------------------
say "安装 Studio 依赖（首次约 2–4 分钟）"
cd "$STUDIO"
[ -f .env.local ] || cp .env.example .env.local
"${PNPM[@]}" install --frozen-lockfile > "$LOGS/studio-install.log" 2>&1 || { tail -n 30 "$LOGS/studio-install.log"; kill "$API_PID" 2>/dev/null; fail "Studio 依赖安装失败，详见 $LOGS/studio-install.log"; }

say "启动 Studio：http://localhost:$STUDIO_PORT"
node scripts/run-framework.mjs dev > "$LOGS/studio.log" 2>&1 &
STUDIO_PID=$!

cleanup() { kill "$STUDIO_PID" "$API_PID" 2>/dev/null || true; wait 2>/dev/null || true; echo; echo "已停止预览。"; }
trap cleanup EXIT INT TERM

say "等待服务就绪"
for _ in $(seq 1 120); do
  if ! kill -0 "$API_PID" 2>/dev/null; then tail -n 30 "$LOGS/api.log"; fail "后端 API 意外退出，详见 $LOGS/api.log"; fi
  if ! kill -0 "$STUDIO_PID" 2>/dev/null; then tail -n 30 "$LOGS/studio.log"; fail "Studio 意外退出，详见 $LOGS/studio.log"; fi
  if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && curl -fsS -o /dev/null "http://localhost:$STUDIO_PORT/extraction" 2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ "${READY:-}" = 1 ] || { tail -n 20 "$LOGS/studio.log"; fail "等待超时，详见 $LOGS 下的日志"; }

URL="http://localhost:$STUDIO_PORT/extraction"
cat <<EOF

✅ 预览已就绪
   地址：$URL
   登录：点击左下角「账户与连接」，粘贴访问凭证
     作者账号：local-admin-token
     审核账号：local-reviewer-token（高风险提案需由另一身份批准）
   日志：$LOGS
   保持此窗口打开；按 Ctrl+C 停止。
EOF
if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
wait
