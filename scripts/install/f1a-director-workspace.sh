#!/usr/bin/env bash
# F1a: install the Director's workspace instructions from the repo.
# Replaces OpenClaw's generic bootstrap files; removes the first-run ritual.
set -euo pipefail
SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/agent/workspace}"
WS=~/filmlab/agent-workspace
for f in AGENTS.md SOUL.md IDENTITY.md USER.md; do
  from="$SRC/$f"
  # the owner's own notes (not published) replace the generic USER.md
  [[ "$f" == USER.md && -f "$SRC/USER.local.md" ]] && from="$SRC/USER.local.md"
  tr -d '\r' < "$from" > "$WS/$f"
  chmod 600 "$WS/$f"
done
rm -f "$WS/BOOTSTRAP.md"
ls -la "$WS"
