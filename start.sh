#!/usr/bin/env bash
# 停止旧服务后启动 Web 和后台任务；按 Ctrl+C 同时停止。
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
unset PYTHONPATH

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "用法：./start.sh [监听地址:端口]"
    echo "默认：0.0.0.0:8000（支持局域网）；仅本机：./start.sh 127.0.0.1:8000"
    echo "每次启动会先停止本项目的旧 Web 和 worker。"
    echo "仅停止服务：./start.sh stop"
    echo "通过 ALLOWED_HOSTS 环境变量配置允许访问的域名或 IP。"
    exit 0
fi
if (( $# > 1 )); then
    echo "参数过多，请运行 ./start.sh --help 查看用法。" >&2
    exit 2
fi

PYTHON="${PWD}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
    echo "缺少 .venv/bin/python，请先按 README.md 创建虚拟环境并安装依赖。" >&2
    exit 1
fi

export PYTHONUNBUFFERED=1
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1,192.168.31.6,172.17.65.13,grow.com}"
# 串行执行停止和启动，防止多个启动命令产生重复 worker。
if ! command -v flock >/dev/null 2>&1; then
    echo "缺少 flock，请先安装 util-linux。" >&2
    exit 1
fi
exec 9>.start.lock
flock -x 9
"$PYTHON" tools/stop_local.py
if [[ "${1:-}" == "stop" ]]; then
    exit 0
fi

"$PYTHON" manage.py check
"$PYTHON" manage.py migrate --noinput

pids=()
cleanup() {
    trap - EXIT INT TERM
    if (( ${#pids[@]} )); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

address="${1:-0.0.0.0:8000}"
echo "启动 Web：${address}，同时启动后台任务。按 Ctrl+C 停止。"
"$PYTHON" manage.py worker 9>&- &
pids+=("$!")
"$PYTHON" manage.py runserver "$address" --noreload 9>&- &
pids+=("$!")
flock -u 9
exec 9>&-

# 任一服务退出时，EXIT trap 会停止另一个服务。
status=0
wait -n "${pids[@]}" || status=$?
echo "服务已退出，正在停止其余进程。" >&2
exit "$status"
