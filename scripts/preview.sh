#!/usr/bin/env bash
# KnowledgeOS 本地预览：准备运行环境，启动后端 API（8787）、编写助手网关（8790）与 Studio（5173），并打开浏览器。
# 用法：bash scripts/preview.sh   或在访达中双击仓库根目录的「启动KnowledgeOS预览.command」
# 缺少 Python ≥ 3.10 或 Node.js ≥ 22.13 时，自动下载到 .preview-tools/（无需管理员权限，不修改系统设置）。
# 适用于 macOS / Linux（bash 3.2 及以上）。按 Ctrl+C 或关闭窗口停止。
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="$ROOT/prototype/ontology-studio"
TOOLS="$ROOT/.preview-tools"
LOGS="$ROOT/runtime/preview-logs"
API_PORT=8787
ASSISTANT_PORT=8790
STUDIO_PORT=5173
# 编写助手模型配置（可选）：runtime/assistant.env，例如
#   KNOWLEDGEOS_ASSISTANT_PROVIDER=anthropic            # 或 openai_compatible
#   KNOWLEDGEOS_ASSISTANT_MODEL=claude-sonnet-5-5
#   KNOWLEDGEOS_ASSISTANT_API_KEY=...
#   KNOWLEDGEOS_ASSISTANT_BASE_URL=...                   # openai_compatible 必填
# 未配置时以“演示模式”启动：返回固定样例、不调用模型，仅用于体验界面流程。
ASSISTANT_ENV="$ROOT/runtime/assistant.env"
mkdir -p "$LOGS" "$TOOLS"
STATUS="$LOGS/status.txt"
exec > >(tee -a "$LOGS/setup.log") 2>&1
echo "==== $(date '+%Y-%m-%d %H:%M:%S') 启动预览 ===="

say() { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; echo "step: $*" > "$STATUS"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; echo "failed: $*" > "$STATUS"; exit 1; }
trap 'code=$?; [ $code -eq 0 ] || { grep -q "^failed:" "$STATUS" 2>/dev/null || echo "failed: exit $code at line $LINENO" > "$STATUS"; }' EXIT

OS="$(uname -s)"; ARCH="$(uname -m)"
case "$OS-$ARCH" in
  Darwin-arm64) NODE_PLATFORM=darwin-arm64; PY_TRIPLE=aarch64-apple-darwin ;;
  Darwin-x86_64) NODE_PLATFORM=darwin-x64; PY_TRIPLE=x86_64-apple-darwin ;;
  Linux-x86_64) NODE_PLATFORM=linux-x64; PY_TRIPLE=x86_64-unknown-linux-gnu ;;
  Linux-aarch64|Linux-arm64) NODE_PLATFORM=linux-arm64; PY_TRIPLE=aarch64-unknown-linux-gnu ;;
  *) fail "暂不支持的系统：$OS $ARCH" ;;
esac
command -v curl >/dev/null 2>&1 || fail "需要 curl。"

# --- Python ≥ 3.10 -----------------------------------------------------------
say "检查 Python（需要 3.10 及以上）"
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
PYTHON=""
for candidate in "$TOOLS/python/bin/python3" python3.13 python3.12 python3.11 python3.10 python3 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  path="$(command -v "$candidate" 2>/dev/null || true)"
  if [ -n "$path" ] && py_ok "$path"; then PYTHON="$path"; break; fi
done
if [ -z "$PYTHON" ]; then
  say "未找到 Python 3.10+，下载独立的 Python 3.12 到 .preview-tools（约 30 MB）"
  PATTERN="cpython-3\.12\.[0-9]+(\+|%2B)[0-9]+-$PY_TRIPLE-install_only\.tar\.gz"
  URL_PY="$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest 2>/dev/null \
    | grep -Eo "https://[^\"]*/$PATTERN" | head -n 1 || true)"
  if [ -z "$URL_PY" ]; then
    TAG="$(curl -fsSL https://raw.githubusercontent.com/astral-sh/python-build-standalone/latest-release/latest-release.json 2>/dev/null \
      | sed -n 's/.*"tag": *"\([0-9]*\)".*/\1/p' || true)"
    [ -n "$TAG" ] && FILE="$(curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/expanded_assets/$TAG" 2>/dev/null \
      | grep -Eo "$PATTERN" | head -n 1 | sed 's/+/%2B/' || true)" && [ -n "$FILE" ] \
      && URL_PY="https://github.com/astral-sh/python-build-standalone/releases/download/$TAG/$FILE"
  fi
  [ -n "$URL_PY" ] || fail "无法获取 Python 下载地址，请检查网络；也可以先安装 Python 3.12（https://www.python.org/downloads/）后重试。"
  echo "下载 $URL_PY"
  curl -fL --progress-bar -o "$TOOLS/python.tar.gz" "$URL_PY" \
    || fail "Python 下载失败，请检查网络后重试。"
  rm -rf "$TOOLS/python" && tar -xzf "$TOOLS/python.tar.gz" -C "$TOOLS" && rm -f "$TOOLS/python.tar.gz"
  PYTHON="$TOOLS/python/bin/python3"
  py_ok "$PYTHON" || fail "下载的 Python 无法运行。"
fi
echo "使用 $("$PYTHON" --version) ($PYTHON)"

# --- Node.js ≥ 22.13 ----------------------------------------------------------
say "检查 Node.js（需要 22.13 及以上）"
node_ok() { "$1" -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22||(a===22&&b>=13)?0:1)' >/dev/null 2>&1; }
NODE=""
for candidate in "$TOOLS/node/bin/node" node /opt/homebrew/bin/node /usr/local/bin/node; do
  path="$(command -v "$candidate" 2>/dev/null || true)"
  if [ -n "$path" ] && node_ok "$path"; then NODE="$path"; break; fi
done
if [ -z "$NODE" ]; then
  say "未找到 Node.js 22.13+，下载 Node.js 22 LTS 到 .preview-tools（约 45 MB）"
  NODE_FILE="$(curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt | grep -o "node-v22\.[0-9.]*-$NODE_PLATFORM\.tar\.gz" | head -n 1)" \
    || fail "无法获取 Node.js 下载信息，请检查网络。"
  [ -n "$NODE_FILE" ] || fail "没有找到适用于 $NODE_PLATFORM 的 Node.js 安装包。"
  curl -fL --progress-bar -o "$TOOLS/node.tar.gz" "https://nodejs.org/dist/latest-v22.x/$NODE_FILE" || fail "Node.js 下载失败，请检查网络后重试。"
  rm -rf "$TOOLS/node" "$TOOLS/${NODE_FILE%.tar.gz}" && tar -xzf "$TOOLS/node.tar.gz" -C "$TOOLS" && rm -f "$TOOLS/node.tar.gz"
  mv "$TOOLS/${NODE_FILE%.tar.gz}" "$TOOLS/node"
  NODE="$TOOLS/node/bin/node"
  node_ok "$NODE" || fail "下载的 Node.js 无法运行。"
fi
export PATH="$(dirname "$NODE"):$PATH"
echo "使用 Node.js $(node --version) ($NODE)"

# --- Ports --------------------------------------------------------------------
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
if curl -fsS -o /dev/null "http://localhost:$STUDIO_PORT/extraction" 2>/dev/null && curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
  echo "ready: http://localhost:$STUDIO_PORT/extraction" > "$STATUS"
  echo "预览已在运行：http://localhost:$STUDIO_PORT/extraction（如需载入新代码，请先关闭原预览窗口）"
  command -v open >/dev/null 2>&1 && open "http://localhost:$STUDIO_PORT/extraction"
  exit 0
fi
for port in $API_PORT $ASSISTANT_PORT $STUDIO_PORT; do
  port_busy "$port" && fail "端口 $port 已被其他程序占用。请关闭占用它的程序后重试（终端中 lsof -i :$port 可查看）。"
done

# --- Backend ------------------------------------------------------------------
say "安装后端依赖（首次约 1–2 分钟）"
if [ ! -x "$ROOT/.venv/bin/python" ] || ! py_ok "$ROOT/.venv/bin/python"; then rm -rf "$ROOT/.venv"; "$PYTHON" -m venv "$ROOT/.venv"; fi
"$ROOT/.venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip || fail "pip 升级失败，详见 $LOGS/setup.log"
"$ROOT/.venv/bin/python" -m pip install -q --disable-pip-version-check -e "$ROOT[test]" || fail "后端依赖安装失败，详见 $LOGS/setup.log"
KOS="$ROOT/.venv/bin/knowledgeos"

say "编译知识库"
"$KOS" --root "$ROOT" rebuild > "$LOGS/rebuild.log" 2>&1 || { tail -n 30 "$LOGS/rebuild.log"; fail "编译失败，详见 $LOGS/rebuild.log"; }

say "准备抽取演示数据（仅首次）"
"$ROOT/.venv/bin/python" "$ROOT/scripts/seed_extraction_demo.py" --root "$ROOT" > "$LOGS/seed.log" 2>&1 \
  && tail -n 1 "$LOGS/seed.log" || echo "演示数据未写入（不影响使用），详见 $LOGS/seed.log"

say "启动后端 API：http://127.0.0.1:$API_PORT"
"$KOS" --root "$ROOT" dev-serve --port "$API_PORT" > "$LOGS/api.log" 2>&1 &
API_PID=$!
STUDIO_PID=""
ASSISTANT_PID=""
cleanup() { [ -n "$STUDIO_PID" ] && kill "$STUDIO_PID" 2>/dev/null; [ -n "$ASSISTANT_PID" ] && kill "$ASSISTANT_PID" 2>/dev/null; kill "$API_PID" 2>/dev/null; wait 2>/dev/null; echo "stopped" > "$STATUS"; echo; echo "已停止预览。"; }
trap 'cleanup' INT TERM HUP

# --- Assistant gateway ----------------------------------------------------------
say "启动编写助手网关：http://127.0.0.1:$ASSISTANT_PORT"
ASSISTANT_MODE="演示模式（固定样例，不调用模型）"
(
  set -a
  [ -f "$ASSISTANT_ENV" ] && . "$ASSISTANT_ENV"
  set +a
  export KNOWLEDGEOS_API_BASE_URL="http://127.0.0.1:$API_PORT"
  export KNOWLEDGEOS_ASSISTANT_PROVIDER="${KNOWLEDGEOS_ASSISTANT_PROVIDER:-demo}"
  exec "$ROOT/.venv/bin/knowledgeos-assistant" --port "$ASSISTANT_PORT"
) > "$LOGS/assistant.log" 2>&1 &
ASSISTANT_PID=$!
if [ -f "$ASSISTANT_ENV" ] && grep -q '^KNOWLEDGEOS_ASSISTANT_PROVIDER=' "$ASSISTANT_ENV" && ! grep -q '^KNOWLEDGEOS_ASSISTANT_PROVIDER=demo' "$ASSISTANT_ENV"; then
  ASSISTANT_MODE="$(sed -n 's/^KNOWLEDGEOS_ASSISTANT_PROVIDER=//p' "$ASSISTANT_ENV" | head -n 1) · $(sed -n 's/^KNOWLEDGEOS_ASSISTANT_MODEL=//p' "$ASSISTANT_ENV" | head -n 1)"
fi

# --- Studio -------------------------------------------------------------------
say "安装 Studio 依赖（首次约 2–4 分钟）"
cd "$STUDIO"
[ -f .env.local ] || cp .env.example .env.local
grep -q '^KNOWLEDGEOS_ASSISTANT_BASE_URL=' .env.local || printf '\nKNOWLEDGEOS_ASSISTANT_BASE_URL=http://127.0.0.1:%s\n' "$ASSISTANT_PORT" >> .env.local
if command -v pnpm >/dev/null 2>&1 && [ "$(pnpm --version 2>/dev/null)" = "11.25.0" ]; then PNPM="pnpm"; else PNPM="npx --yes pnpm@11.25.0"; fi
$PNPM install --frozen-lockfile > "$LOGS/studio-install.log" 2>&1 || { tail -n 30 "$LOGS/studio-install.log"; cleanup; fail "Studio 依赖安装失败，详见 $LOGS/studio-install.log"; }

say "启动 Studio：http://localhost:$STUDIO_PORT"
node scripts/run-framework.mjs dev > "$LOGS/studio.log" 2>&1 &
STUDIO_PID=$!

say "等待服务就绪"
READY=""
for _ in $(seq 1 150); do
  kill -0 "$API_PID" 2>/dev/null || { tail -n 30 "$LOGS/api.log"; cleanup; fail "后端 API 意外退出，详见 $LOGS/api.log"; }
  kill -0 "$STUDIO_PID" 2>/dev/null || { tail -n 30 "$LOGS/studio.log"; cleanup; fail "Studio 意外退出，详见 $LOGS/studio.log"; }
  kill -0 "$ASSISTANT_PID" 2>/dev/null || { tail -n 30 "$LOGS/assistant.log"; cleanup; fail "助手网关意外退出，详见 $LOGS/assistant.log"; }
  if curl -fsS "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && curl -fsS -o /dev/null "http://localhost:$STUDIO_PORT/extraction" 2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || { tail -n 20 "$LOGS/studio.log"; cleanup; fail "等待超时，详见 $LOGS 下的日志"; }

URL="http://localhost:$STUDIO_PORT/extraction"
echo "ready: $URL" > "$STATUS"
cat <<EOF

✅ 预览已就绪
   地址：$URL
   登录：点击左下角「账户与连接」，粘贴访问凭证
     作者账号：local-admin-token
     审核账号：local-reviewer-token（高风险提案需由另一身份批准）
   编写助手：$ASSISTANT_MODE
     在 /rules、/templates、/extraction 与本体白板「建模助手」中使用；配置真实模型见 runtime/assistant.env（说明见 docs/authoring-assistant.md）
   日志：$LOGS
   保持此窗口打开；按 Ctrl+C 或关闭窗口停止。
EOF
if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
wait "$STUDIO_PID" || true
cleanup
