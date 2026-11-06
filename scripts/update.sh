#!/usr/bin/env bash
# Update FilmLab to a git ref (a tag such as v0.7.0, or a branch), inside the FilmLab distro.
#   update.sh <ref>
# 1. refuses while renders or exports run, or with uncommitted changes in the repo;
# 2. takes a backup (kind pre-update) through the running backend, or a database copy if it's down;
# 3. records the current commit in ~/filmlab/update-history so rollback.sh can return to it;
# 4. checks out <ref>, builds the cockpit, reinstalls the director's workspace, restarts the backend
#    (which applies new migrations, keeping its own pre-migration copy) and checks /api/health.
# It never fetches, pulls or pushes on its own: fetch first if the ref is new.
set -euo pipefail
source ~/.profile
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # this checkout, wherever it lives
ref="${1:?usage: update.sh <git ref>}"
cd "$REPO"
git rev-parse --verify --quiet "$ref^{commit}" >/dev/null || { echo "unknown ref $ref (git fetch first?)" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "the repo has uncommitted changes; commit or stash them first" >&2; exit 1; }
DB=~/filmlab/data/filmlab.sqlite
busy=$(python3 -c "
import sqlite3; c = sqlite3.connect('file:$DB?mode=ro', uri=True)
a = ('submitting','queued','running','cancelling','restored')
q = lambda t: c.execute(f'SELECT COUNT(*) FROM {t} WHERE status IN {a}').fetchone()[0]
print(q('render_jobs') + q('image_jobs') + c.execute(\"SELECT COUNT(*) FROM exports WHERE status IN ('queued','running')\").fetchone()[0])")
[ "$busy" = "0" ] || { echo "$busy render/image/export job(s) still running; wait or cancel them first" >&2; exit 1; }

from=$(git rev-parse HEAD)
stamp=$(date -u +%Y%m%d-%H%M%S)
python3 - "$DB" "$HOME/filmlab/data/filmlab-pre-update-$stamp.sqlite" <<'PY'
import sqlite3, sys
src, dest = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dest:
    src.backup(dest)
print("database copied to", sys.argv[2])
PY
echo "$stamp $from $ref $HOME/filmlab/data/filmlab-pre-update-$stamp.sqlite" >> ~/filmlab/update-history
git checkout --quiet "$ref"
echo "checked out $ref ($(git rev-parse --short HEAD)), was $(git rev-parse --short "$from")"
(cd backend && UV_PROJECT_ENVIRONMENT=~/filmlab/venv uv sync --frozen -q) && echo "python dependencies in sync"  # the new version may need new ones
bash scripts/build-frontend.sh | tail -1
bash scripts/install/f1a-director-workspace.sh >/dev/null
bash scripts/filmlabctl.sh restart-backend
sleep 3
curl -sk --max-time 10 https://127.0.0.1:19300/api/health -o /dev/null -w "health endpoint: HTTP %{http_code} (401 = up, needs login)\n" \
  || curl -s --max-time 10 http://127.0.0.1:19300/api/health -o /dev/null -w "health endpoint: HTTP %{http_code}\n"
echo "update done; to go back: bash scripts/rollback.sh"
