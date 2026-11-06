#!/usr/bin/env bash
# Undo the last update.sh: check out the commit FilmLab ran before it and, if that update changed the
# database (a migration ran), put back the database copy taken just before the update. Media is untouched.
#   rollback.sh            the last update
# The current database is kept as filmlab-pre-rollback-<time>.sqlite first.
set -euo pipefail
source ~/.profile
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # this checkout, wherever it lives
HIST=~/filmlab/update-history
DB=~/filmlab/data/filmlab.sqlite
[ -s "$HIST" ] || { echo "no update recorded in $HIST" >&2; exit 1; }
read -r stamp from ref copy < <(tail -1 "$HIST")
cd "$REPO"
[ -z "$(git status --porcelain)" ] || { echo "the repo has uncommitted changes; commit or stash them first" >&2; exit 1; }
echo "rolling back the update to $ref of $stamp: back to $(git rev-parse --short "$from")"
migrated=$(python3 -c "
import sqlite3
n = lambda p: {r[0] for r in sqlite3.connect(f'file:{p}?mode=ro', uri=True).execute('SELECT name FROM schema_migrations')}
print('yes' if n('$DB') - n('$copy') else 'no')")
read -r -p "Continue? [yes/NO] " answer
[ "$answer" = "yes" ] || { echo "nothing changed"; exit 0; }
systemctl --user stop filmlab-backend || true
if [ "$migrated" = "yes" ]; then
  now=$(date -u +%Y%m%d-%H%M%S)
  python3 - "$DB" "$HOME/filmlab/data/filmlab-pre-rollback-$now.sqlite" <<'PY'
import sqlite3, sys
src, dest = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dest:
    src.backup(dest)
PY
  rm -f "$DB-wal" "$DB-shm"
  cp -p "$copy" "$DB"
  echo "database put back from $copy (work since the update is in filmlab-pre-rollback-$now.sqlite)"
fi
git checkout --quiet "$from"
(cd backend && UV_PROJECT_ENVIRONMENT=~/filmlab/venv uv sync --frozen -q) && echo "python dependencies in sync"  # the new version may need new ones
bash scripts/build-frontend.sh | tail -1
bash scripts/install/f1a-director-workspace.sh >/dev/null
bash scripts/filmlabctl.sh restart-backend
sed -i '$ d' "$HIST"
echo "rolled back to $(git rev-parse --short HEAD)"
