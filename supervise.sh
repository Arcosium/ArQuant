#!/bin/bash
# Arquant v1.0 — watchdog. Keeps the uvicorn server AND the Cloudflare tunnel alive.
# Run once in the background:  nohup /home/opc/projects/Arquant/supervise.sh > /tmp/arquant_supervise.log 2>&1 &
# (or add to crontab:  @reboot /home/opc/projects/Arquant/supervise.sh >> /tmp/arquant_supervise.log 2>&1 )
set -u
APP_DIR="/home/opc/projects/Arquant"
PORT=8500
APP_LOG="/tmp/arquant.log"
TUN_LOG="/tmp/cloudflared_arquant.log"
TUN_CFG="/home/opc/.cloudflared/config.yml"
PY="python3.11"

start_server() {
  echo "$(date -u +%FT%TZ) [supervise] starting uvicorn on :$PORT"
  ( cd "$APP_DIR" && find . -name __pycache__ -exec rm -rf {} + 2>/dev/null;
    nohup $PY -m uvicorn server.app:app --host 0.0.0.0 --port $PORT >> "$APP_LOG" 2>&1 & )
}
start_tunnel() {
  echo "$(date -u +%FT%TZ) [supervise] starting cloudflared (hyfe-iqc)"
  nohup cloudflared tunnel --config "$TUN_CFG" run hyfe-iqc >> "$TUN_LOG" 2>&1 < /dev/null &
}

server_up() { curl -fsS -m4 "http://localhost:$PORT/health" >/dev/null 2>&1; }
tunnel_up() { pgrep -f "cloudflared tunnel --config $TUN_CFG run hyfe-iqc" >/dev/null 2>&1; }

while true; do
  server_up || { echo "$(date -u +%FT%TZ) [supervise] server DOWN → restart"; start_server; sleep 5; }
  tunnel_up || { echo "$(date -u +%FT%TZ) [supervise] tunnel DOWN → restart"; start_tunnel; sleep 5; }
  sleep 20
done
