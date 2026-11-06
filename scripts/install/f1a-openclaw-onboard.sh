#!/usr/bin/env bash
# F1a: non-interactive OpenClaw onboarding for the FilmLab director.
# Run inside the FilmLab distro as user filmlab. Installs no service, no
# channels, no skills; gateway is loopback-only on 19189 with token auth
# whose secret lives in ~/.config/filmlab/secrets.env (not in openclaw config).
set -euo pipefail
source ~/.profile

SECRETS=~/.config/filmlab/secrets.env
WORKSPACE=~/filmlab/agent-workspace
mkdir -p "$(dirname "$SECRETS")" "$WORKSPACE"
umask 077
if ! grep -q '^OPENCLAW_GATEWAY_TOKEN=' "$SECRETS" 2>/dev/null; then
  echo "OPENCLAW_GATEWAY_TOKEN=$(openssl rand -hex 32)" >> "$SECRETS"
fi
chmod 600 "$SECRETS"
set -a; source "$SECRETS"; set +a

openclaw onboard --non-interactive --accept-risk \
  --mode local --flow manual \
  --auth-choice ollama \
  --gateway-port 19189 --gateway-bind loopback \
  --gateway-auth token --gateway-token-ref-env OPENCLAW_GATEWAY_TOKEN \
  --workspace "$WORKSPACE" --agent-name Director \
  --skip-channels --skip-daemon --no-install-daemon \
  --skip-skills --skip-search --skip-hooks --skip-ui --skip-health \
  --suppress-gateway-token-output
