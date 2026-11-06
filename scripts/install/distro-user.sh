#!/usr/bin/env bash
# FilmLab installer, step "filmlab" (run as user filmlab inside the FilmLab distro by Setup-FilmLab.ps1). Idempotent.
# The director (OpenClaw, locked down), the backend's Python environment and the cockpit.
#
#   distro-user.sh [director model]     default gemma4:31b-cloud (an Ollama cloud model: no GPU, needs sign-in)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODEL="${1:-gemma4:31b-cloud}"
OPENCLAW_VERSION="${OPENCLAW_VERSION:-2026.10.10}"   # the version FilmLab's director policy was reviewed against

if ! grep -q 'npm-global' ~/.profile; then
  echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> ~/.profile
fi
source ~/.profile
mkdir -p ~/.npm-global ~/.config/filmlab ~/filmlab
npm config set prefix ~/.npm-global

if ! openclaw --version 2>/dev/null | grep -q "$OPENCLAW_VERSION"; then
  echo "installing OpenClaw $OPENCLAW_VERSION (the director's runtime)"
  npm install -g "openclaw@$OPENCLAW_VERSION" --no-audit --no-fund --loglevel=error
fi

if [[ ! -f ~/.openclaw/openclaw.json ]]; then
  echo "setting up the director (gateway on loopback 19189, token in ~/.config/filmlab/secrets.env)"
  bash "$REPO/scripts/install/f1a-openclaw-onboard.sh" >/dev/null
fi
echo "locking the director down (minimal tools, every dangerous group denied), model $MODEL"
FILMLAB_DIRECTOR_MODEL="$MODEL" bash "$REPO/scripts/install/f1a-director-policy.sh" >/dev/null
bash "$REPO/scripts/install/f1a-director-workspace.sh" "$REPO/agent/workspace" >/dev/null

echo "installing the backend's Python environment"
cd "$REPO/backend"
[[ -x ~/filmlab/venv/bin/python ]] || uv venv ~/filmlab/venv --python 3.12 -q
UV_PROJECT_ENVIRONMENT=~/filmlab/venv uv sync --frozen -q

echo "building the cockpit"
bash "$REPO/scripts/build-frontend.sh" "$REPO" >/dev/null

echo "$REPO" > ~/.config/filmlab/repo
echo "FilmLab installed from $REPO"
