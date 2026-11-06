#!/usr/bin/env bash
# filmlabctl (inside the FilmLab distro): manual control of FilmLab services.
# Services run as transient systemd --user units: nothing is enabled at boot.
#   filmlabctl.sh status | start | stop | restart | logs [gateway|backend] [N]
set -euo pipefail
source ~/.profile
SECRETS=~/.config/filmlab/secrets.env
# Optional, non-secret overrides (F1c: TLS + LAN bind). Delete it to return to loopback HTTP.
BACKEND_ENV=~/.config/filmlab/filmlab.env
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # this checkout, wherever it lives
GATEWAY_UNIT=filmlab-gateway
BACKEND_UNIT=filmlab-backend
GATEWAY_PORT=19189
BACKEND_PORT=19300
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

wait_listen() {  # port seconds name
  for _ in $(seq 1 "$2"); do
    local addr; addr=$(ss -ltnH "sport = :$1" | awk '{print $4; exit}')
    [[ -n "$addr" ]] && { echo "$3 started on $addr"; return; }
    sleep 1
  done
  echo "$3 did not start listening within $2s; see: filmlabctl logs $3" >&2; return 1
}

start_unit() {  # unit description port name cmd...
  local unit=$1 desc=$2 port=$3 name=$4; shift 4
  if systemctl --user is-active --quiet "$unit"; then echo "$name already running"; return; fi
  systemctl --user reset-failed "$unit" 2>/dev/null || true
  systemd-run --user --unit="$unit" --description="$desc" \
    --property=EnvironmentFile="$SECRETS" --setenv=PATH="$PATH" "${UNIT_EXTRA[@]}" "$@" >/dev/null
  wait_listen "$port" 45 "$name"
}

start_gateway() {
  UNIT_EXTRA=()
  start_unit "$GATEWAY_UNIT" "FilmLab OpenClaw Gateway (manual)" "$GATEWAY_PORT" gateway \
    "$(command -v openclaw)" gateway --port "$GATEWAY_PORT"
}

start_backend() {
  UNIT_EXTRA=(--working-directory="$REPO/backend" --setenv=FILMLAB_STATIC_DIR="$HOME/filmlab/app/static"
              --setenv=FILMLAB_PORT="$BACKEND_PORT" --setenv=PYTHONUNBUFFERED=1
              --property=EnvironmentFile=-"$BACKEND_ENV"
              # Stop sends SIGTERM to the backend only, so it can stop ffmpeg and keep exports resumable;
              # anything left after it exits is killed.
              --property=KillMode=mixed)
  start_unit "$BACKEND_UNIT" "FilmLab Studio backend (manual)" "$BACKEND_PORT" backend \
    "$HOME/filmlab/venv/bin/python" -m app.main
}

stop_unit() {
  systemctl --user stop "$1" 2>/dev/null && echo "$2 stopped" || echo "$2 not running"
}

status() {
  printf 'gateway unit: %s\n' "$(systemctl --user is-active "$GATEWAY_UNIT" 2>/dev/null || true)"
  printf 'backend unit: %s\n' "$(systemctl --user is-active "$BACKEND_UNIT" 2>/dev/null || true)"
  echo "listeners:"; ss -ltn | awk 'NR==1 || /:(19189|19190|19191|19300) /'
}

case "${1:-status}" in
  start)   start_gateway; start_backend ;;
  stop)    stop_unit "$BACKEND_UNIT" backend; stop_unit "$GATEWAY_UNIT" gateway ;;
  restart) stop_unit "$BACKEND_UNIT" backend; stop_unit "$GATEWAY_UNIT" gateway; start_gateway; start_backend ;;
  restart-backend) stop_unit "$BACKEND_UNIT" backend; start_backend ;;
  status)  status ;;
  logs)
    unit=$GATEWAY_UNIT; [ "${2:-backend}" = backend ] && unit=$BACKEND_UNIT
    journalctl --user -u "$unit" -n "${3:-80}" --no-pager ;;
  *) echo "usage: filmlabctl.sh status|start|stop|restart|restart-backend|logs [gateway|backend] [N]" >&2; exit 2 ;;
esac
