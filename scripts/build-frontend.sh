#!/usr/bin/env bash
# Build the cockpit inside the FilmLab distro (node_modules on ext4, not /mnt/d)
# and install the result to ~/filmlab/app/static. Keeps package-lock.json in the repo.
set -euo pipefail
source ~/.profile
REPO="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BUILD=~/filmlab/build/frontend
OUT=~/filmlab/app/static

mkdir -p "$BUILD"
find "$BUILD" -mindepth 1 -maxdepth 1 ! -name node_modules -exec rm -rf {} +
( cd "$REPO/frontend" && tar cf - --exclude=node_modules --exclude=dist . ) | ( cd "$BUILD" && tar xf - )
cd "$BUILD"
if [ -f package-lock.json ]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
  cp package-lock.json "$REPO/frontend/package-lock.json"
fi
npm run build
rm -rf "$OUT" && mkdir -p "$(dirname "$OUT")" && cp -r dist "$OUT"
echo "cockpit installed to $OUT"
