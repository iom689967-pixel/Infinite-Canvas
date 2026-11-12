#!/usr/bin/env bash
# 启动一个与正式服务隔离的测试实例，供 repro.js / backend_checks.py 使用。
#
#   tests/repro/run_test_instance.sh start <data-source-dir> [port]
#   tests/repro/run_test_instance.sh stop
#   tests/repro/run_test_instance.sh status
#
# - 把当前工作区的代码（不含 .git、.venv、用户数据和资产）同步到 $IC_TEST_DIR；
# - 每次 start 都从 <data-source-dir>（例如 data/ 的备份）重新复制一份数据，不带 media_previews / update_backups；
# - API/.env 写入假密钥，测试实例不会拿到真实 Key，也就不会产生真实计费；
# - 只监听 127.0.0.1。
#
# 试用模式（可选环境变量）：
#   IC_ENV_FILE=<路径>      用这个文件作为 API/.env（例如真实的 API/.env；注意会产生真实调用和计费）
#   IC_ASSETS_SRC=<目录>    把素材目录复制为 assets/（macOS 上用 APFS 克隆，几乎不占额外空间）
#   IC_HISTORY_SRC=<文件>   复制 history.json
#   IC_PRESERVE_STATE=1     实例目录里已有数据时不再覆盖 data/、assets/、API/.env（重启试用实例时用）
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
IC_TEST_DIR="${IC_TEST_DIR:-${TMPDIR:-/tmp}/ic-audit-test}"
PID_FILE="$IC_TEST_DIR/.test-instance.pid"
LOG_FILE="$IC_TEST_DIR/.test-instance.log"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
# 测试实例使用的假 ModelScope Key；backend_checks.py 用它检查 /api/config/token 是否掩码。
DUMMY_MS_KEY="ms-audit-dummy-key-0000-1234abcd"

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

case "${1:-}" in
    start)
        DATA_SRC="${2:?usage: start <data-source-dir> [port]}"
        PORT="${3:-3100}"
        stop_instance
        mkdir -p "$IC_TEST_DIR"
        rsync -a --delete \
            --exclude '/.git' --exclude '/.venv' --exclude '/.python' --exclude '/.uv-cache' --exclude '/.tools' \
            --exclude '/python' --exclude '/packages' --exclude '/assets' --exclude '/output' --exclude '/data' \
            --exclude '/history.json' --exclude '/API/.env' --exclude '/.runtime' --exclude '/.launchd' \
            --exclude '__pycache__' --exclude '/tests/repro/results*.json' \
            --exclude '/.test-instance.pid' --exclude '/.test-instance.log' \
            "$REPO_ROOT/" "$IC_TEST_DIR/"
        if [[ "${IC_PRESERVE_STATE:-}" == "1" && -d "$IC_TEST_DIR/data" ]]; then
            echo "keeping existing data/assets/API .env in $IC_TEST_DIR"
        else
            rm -rf "$IC_TEST_DIR/data" "$IC_TEST_DIR/assets"
            rsync -a --exclude 'media_previews' --exclude 'update_backups' "$DATA_SRC/" "$IC_TEST_DIR/data/"
            if [[ -n "${IC_ASSETS_SRC:-}" ]]; then
                cp -cR "$IC_ASSETS_SRC" "$IC_TEST_DIR/assets" 2>/dev/null || cp -R "$IC_ASSETS_SRC" "$IC_TEST_DIR/assets"
            fi
            if [[ -n "${IC_HISTORY_SRC:-}" ]]; then
                cp "$IC_HISTORY_SRC" "$IC_TEST_DIR/history.json"
            fi
            mkdir -p "$IC_TEST_DIR/API"
            if [[ -n "${IC_ENV_FILE:-}" ]]; then
                cp "$IC_ENV_FILE" "$IC_TEST_DIR/API/.env"
                chmod 600 "$IC_TEST_DIR/API/.env"
            else
                printf 'MODELSCOPE_API_KEY=%s\n' "$DUMMY_MS_KEY" > "$IC_TEST_DIR/API/.env"
            fi
        fi
        (
            cd "$IC_TEST_DIR"
            # 清掉可能从外部继承的 Key，确保实例只看到自己 API/.env 里的值。
            env -u COMFLY_API_KEY -u KIE_API_KEY -u RUNNINGHUB_API_KEY -u ARK_API_KEY -u MODELSCOPE_API_KEY \
                nohup "$PYTHON_BIN" -c "import uvicorn, main; uvicorn.run(main.app, host='127.0.0.1', port=$PORT, ws_ping_interval=None, ws_ping_timeout=None)" \
                > "$LOG_FILE" 2>&1 &
            echo $! > "$PID_FILE"
        )
        for _ in $(seq 1 100); do
            if curl -fsS "http://127.0.0.1:$PORT/api/app-info" >/dev/null 2>&1; then
                echo "test instance up: http://127.0.0.1:$PORT  dir=$IC_TEST_DIR  pid=$(cat "$PID_FILE")"
                exit 0
            fi
            sleep 0.3
        done
        echo "test instance failed to start; log:" >&2
        tail -40 "$LOG_FILE" >&2
        exit 1
        ;;
    stop)
        stop_instance
        echo "stopped"
        ;;
    status)
        if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
            echo "running pid=$(cat "$PID_FILE") dir=$IC_TEST_DIR"
        else
            echo "not running"
        fi
        ;;
    *)
        echo "usage: $0 start <data-source-dir> [port] | stop | status" >&2
        exit 2
        ;;
esac
