#!/bin/bash
# 智象 GEO 采集机一键启动。
#
# 做三件事:
#   1. venv 不存在/依赖缺失时自动创建并安装(pip 按本机 Python 自动选兼容的 playwright);
#   2. 带 --remote-debugging-port 的真 Chrome 没在跑时拉起它(已在跑则直接复用);
#   3. 启动 worker。
#
# 用法(在 ~/Documents/geo-worker 下):
#   ./start.sh        前台启动(Ctrl+C 只停 worker,不动 Chrome)
#   ./start.sh -d     后台启动(日志追加到 worker.log)
#   ./start.sh stop   停止后台 worker(不动 Chrome)
#
# 兼容老 Mac 自带的 bash 3.2,勿用 bash 4+ 语法。
set -euo pipefail

cd "$(dirname "$0")"

PROFILE_DIR="$HOME/geo-chrome-profile"
CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

die() { echo "[start] 错误:$*" >&2; exit 1; }

# ---------- stop ----------
if [ "${1:-}" = "stop" ]; then
    if pkill -f "[c]ollector_worker.py" 2>/dev/null; then
        echo "[start] worker 已停止(Chrome 保留)"
    else
        echo "[start] 没有在运行的 worker"
    fi
    exit 0
fi

# ---------- 配置 ----------
[ -f .env ] || die "缺 .env:先 cp .env.example .env,填 GEO_COLLECTOR_TOKEN / GEO_WORKER_CAPABILITIES"
set -a
. ./.env
set +a
[ -n "${GEO_COLLECTOR_TOKEN:-}" ] || die ".env 里没配 GEO_COLLECTOR_TOKEN"

# CDP 地址默认本机 9222;脚本只负责拉起【本机】Chrome,配了远程 CDP 就别用本脚本管 Chrome
CDP_URL="${GEO_WORKER_CDP_URL:-http://127.0.0.1:9222}"
export GEO_WORKER_CDP_URL="$CDP_URL"
CDP_PORT="${CDP_URL##*:}"
CDP_PORT="${CDP_PORT%%/*}"
case "$CDP_PORT" in
    ''|*[!0-9]*) die "从 GEO_WORKER_CDP_URL(${CDP_URL})解析不出端口号" ;;
esac

# ---------- venv 与依赖 ----------
if [ ! -x .venv/bin/python ]; then
    echo "[start] 首次运行:创建 venv 并安装依赖..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
elif ! .venv/bin/python -c "import httpx, playwright" >/dev/null 2>&1; then
    echo "[start] 依赖不全,补装..."
    .venv/bin/pip install -r requirements.txt
fi

# ---------- 真 Chrome(CDP)----------
cdp_up() { curl -s --max-time 2 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; }

if cdp_up; then
    echo "[start] CDP Chrome 已在运行(端口 ${CDP_PORT}),直接复用"
else
    [ -x "$CHROME_BIN" ] || die "未找到 Google Chrome:${CHROME_BIN}"
    echo "[start] 拉起带调试端口的真 Chrome(profile:${PROFILE_DIR})..."
    nohup "$CHROME_BIN" \
        --remote-debugging-port="${CDP_PORT}" \
        --user-data-dir="${PROFILE_DIR}" >/dev/null 2>&1 &
    i=0
    until cdp_up; do
        i=$((i + 1))
        [ "$i" -ge 30 ] && die "等了 30s CDP(端口 ${CDP_PORT})仍未就绪,请手动检查 Chrome"
        sleep 1
    done
    echo "[start] Chrome CDP 就绪。⚠️ 新 profile 首次使用,记得在这个 Chrome 里手动登录各引擎(chat.deepseek.com 等)"
fi

# ---------- worker ----------
if pgrep -f "[c]ollector_worker.py" >/dev/null 2>&1; then
    die "worker 已在运行(pid $(pgrep -f '[c]ollector_worker.py' | head -1));要重启先 ./start.sh stop"
fi

if [ "${1:-}" = "-d" ]; then
    nohup .venv/bin/python collector_worker.py >> worker.log 2>&1 &
    echo "[start] worker 已后台启动(pid $!),看日志:tail -f $(pwd)/worker.log"
else
    echo "[start] 前台启动 worker(Ctrl+C 停止)..."
    exec .venv/bin/python collector_worker.py
fi
