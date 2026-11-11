#!/usr/bin/env bash
# 以“独立实例”模式（INSTANCE_* 环境变量，公测部署用的模式）启动当前代码，供 instance_smoke.js 使用。
#
#   tests/repro/run_instance_mode.sh start [port]
#   tests/repro/run_instance_mode.sh stop
#   tests/repro/run_instance_mode.sh status
#
# - 数据根目录是全新的临时目录 $IC_INSTANCE_DIR/root，不读写仓库里的 data/、API/、assets/；
# - 账号 smoke 的密码随机生成，只写在 $IC_INSTANCE_DIR/password（权限 600）；
# - 预置一个假的 openai 平台 smoke-images，地址指向一个没有服务监听的本机端口（同时登记为允许的上游），
#   生成请求由浏览器测试拦截，不会访问任何真实上游；
# - 只监听 127.0.0.1；环境变量从空白开始（env -i），不会继承任何真实 Key。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
IC_INSTANCE_DIR="${IC_INSTANCE_DIR:-${TMPDIR:-/tmp}/ic-instance-smoke}"
INSTANCE_NAME="smoke"
PID_FILE="$IC_INSTANCE_DIR/instance.pid"
LOG_FILE="$IC_INSTANCE_DIR/instance.log"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
CLEAN_ENV=(env -i "HOME=$HOME" "PATH=/usr/bin:/bin:/usr/sbin:/sbin" "LANG=${LANG:-en_US.UTF-8}")

stop_instance() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

free_port() {
    "$PYTHON_BIN" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

case "${1:-}" in
    start)
        PORT="${2:-3110}"
        stop_instance
        rm -rf "$IC_INSTANCE_DIR/root"
        mkdir -p "$IC_INSTANCE_DIR/root"   # init 要求空目录；data/ 在 init 之后再建
        chmod 700 "$IC_INSTANCE_DIR"
        ADMIN=("$PYTHON_BIN" "$REPO_ROOT/instance_admin.py" --data-root "$IC_INSTANCE_DIR/root" --instance-id "$INSTANCE_NAME")
        (cd "$REPO_ROOT" && "${CLEAN_ENV[@]}" "${ADMIN[@]}" init >/dev/null)
        PASSWORD="$("$PYTHON_BIN" -c 'import secrets; print("Smoke-" + secrets.token_urlsafe(18))')"
        (umask 077; printf '%s' "$PASSWORD" > "$IC_INSTANCE_DIR/password")
        (cd "$REPO_ROOT" && printf '%s' "$PASSWORD" | "${CLEAN_ENV[@]}" "${ADMIN[@]}" create --username "$INSTANCE_NAME" --password-stdin >/dev/null)
        UPSTREAM_PORT="$(free_port)"
        mkdir -p "$IC_INSTANCE_DIR/root/data"
        cat > "$IC_INSTANCE_DIR/root/data/api_providers.json" <<JSON
[{"id": "smoke-images", "name": "Smoke Images", "enabled": true, "primary": true, "protocol": "openai",
  "base_url": "http://127.0.0.1:${UPSTREAM_PORT}/v1", "image_models": ["gpt-image-2"], "chat_models": [], "video_models": []}]
JSON
        (
            cd "$REPO_ROOT"
            "${CLEAN_ENV[@]}" INSTANCE_ID="$INSTANCE_NAME" INSTANCE_DATA_ROOT="$IC_INSTANCE_DIR/root" \
                INSTANCE_HOST=127.0.0.1 INSTANCE_PORT="$PORT" INSTANCE_AUTH_ALLOW_HTTP_LOOPBACK=1 \
                INSTANCE_MOCK_UPSTREAMS="127.0.0.1:${UPSTREAM_PORT}" \
                nohup "$PYTHON_BIN" main.py > "$LOG_FILE" 2>&1 &
            echo $! > "$PID_FILE"
        )
        for _ in $(seq 1 150); do
            if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
                echo "instance up: http://127.0.0.1:$PORT  user=$INSTANCE_NAME  password_file=$IC_INSTANCE_DIR/password  root=$IC_INSTANCE_DIR/root  pid=$(cat "$PID_FILE")"
                exit 0
            fi
            if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then break; fi
            sleep 0.2
        done
        echo "instance failed to start; log:" >&2
        tail -40 "$LOG_FILE" >&2
        exit 1
        ;;
    stop)
        stop_instance
        echo "stopped"
        ;;
    status)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "running pid=$(cat "$PID_FILE") dir=$IC_INSTANCE_DIR"
        else
            echo "not running"
        fi
        ;;
    *)
        echo "usage: $0 start [port] | stop | status" >&2
        exit 2
        ;;
esac
