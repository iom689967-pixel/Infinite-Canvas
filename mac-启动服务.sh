#!/bin/bash
cd "$(dirname "$0")"

# 默认只允许本机访问（服务没有登录认证）。
# 需要让同一局域网的其他设备访问时，显式开启：
#   INFINITE_CANVAS_ALLOW_LAN=1 ./mac-启动服务.sh
APP_URL="http://127.0.0.1:3000/"
case "$(printf '%s' "${INFINITE_CANVAS_ALLOW_LAN:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
    if [ -z "$LAN_IP" ]; then
      LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    if [ -n "$LAN_IP" ]; then
      APP_URL="http://${LAN_IP}:3000/"
    fi
    echo "[安全提示] 已开启局域网访问，同一网络内的设备都能打开本服务，请只在可信网络中使用。"
    ;;
esac

echo "Starting ComfyUI-API-Modelscope..."
echo "Visit: ${APP_URL}"
echo "Local: http://127.0.0.1:3000/"
echo "Press Ctrl+C to stop."
echo ""

# Open browser after 3 seconds
sleep 3 && open "${APP_URL}" &

if [ -x "$PWD/.venv/bin/python" ]; then
  "$PWD/.venv/bin/python" main.py
else
  python3 main.py
fi

echo ""
echo "Server stopped."
