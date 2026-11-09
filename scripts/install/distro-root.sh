#!/usr/bin/env bash
# FilmLab installer, step "distro" (run as ROOT inside a new FilmLab distro by Setup-FilmLab.ps1). Idempotent.
#
#   distro-root.sh            user, /etc/wsl.conf, packages, Node 24, uv
#   distro-root.sh --linger   after the distro restarted with systemd: let the user's services run without a login
#
# The user 'filmlab' has no sudo and a locked password: FilmLab never needs root after this.
set -euo pipefail
USER_NAME=filmlab
export DEBIAN_FRONTEND=noninteractive

if [[ "${1:-}" == "--linger" ]]; then
  loginctl enable-linger "$USER_NAME"
  echo "linger enabled for $USER_NAME"
  exit 0
fi

if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USER_NAME"
  echo "created user $USER_NAME"
fi
passwd -l "$USER_NAME" >/dev/null

cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true
[user]
default=filmlab
[interop]
appendWindowsPath=false
EOF

echo "installing packages (this takes a few minutes the first time)"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git jq sqlite3 ffmpeg openssl python3 python3-venv fonts-dejavu-core >/dev/null  # fonts: the Storybook's pages

if ! node --version 2>/dev/null | grep -q '^v24\.'; then
  echo "installing Node.js 24 (NodeSource)"
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash - >/dev/null
  apt-get install -y -q nodejs >/dev/null
fi

if ! command -v uv >/dev/null; then
  echo "installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh >/dev/null
fi

echo "system ready: $(node --version), $(python3 --version), $(uv --version), $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
