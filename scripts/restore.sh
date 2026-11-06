#!/usr/bin/env bash
# Restore FilmLab from a backup (inside the FilmLab distro). Destructive for the *current* data, which is
# kept aside first, so a restore can itself be undone by restoring that copy.
#   restore.sh <backup folder name | path>      e.g. restore.sh 20261104-120000-manual
# Stops the backend (only FilmLab's unit), keeps ~/filmlab/data and ~/filmlab/media as
# ~/filmlab/pre-restore-<time>/, copies the backup's database and media in, and starts the backend again.
# The backend applies any newer migrations on start (taking its own pre-migration copy).
set -euo pipefail
source ~/.profile
BACKUPS="${FILMLAB_BACKUP_DIR:-$HOME/filmlab/backups}"
DATA="${FILMLAB_DATA_DIR:-$HOME/filmlab/data}"
MEDIA="${FILMLAB_MEDIA_DIR:-$HOME/filmlab/media}"
# FILMLAB_NO_SERVICE=1 restores into the folders above without stopping or starting the backend (drills).
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # this checkout, wherever it lives
src="${1:?usage: restore.sh <backup name or path>}"
# Guard against a wrong target: both folders must be named data/media, side by side, below a home or /tmp folder.
for d in "$DATA" "$MEDIA"; do
  case "$d" in /home/*/data|/home/*/media|/tmp/*/data|/tmp/*/media) ;; *) echo "refusing to restore into $d" >&2; exit 1 ;; esac
done
[ "$(dirname "$DATA")" = "$(dirname "$MEDIA")" ] || { echo "data and media must sit in the same folder" >&2; exit 1; }
[ -d "$src" ] || src="$BACKUPS/$src"
[ -f "$src/manifest.json" ] && [ -f "$src/filmlab.sqlite" ] || { echo "not a FilmLab backup: $src" >&2; exit 1; }

python3 - "$src" <<'PY'
import json, sqlite3, sys
src = sys.argv[1]
c = sqlite3.connect(f"file:{src}/filmlab.sqlite?mode=ro", uri=True)
ok = c.execute("PRAGMA integrity_check").fetchone()[0]
m = json.load(open(f"{src}/manifest.json"))
print(f"backup {m['name']} ({m['kind']}, {m['created_at']}, FilmLab {m.get('version')}): database {ok}, "
      f"{len(m['files'])} files, {m['counts']}")
sys.exit(0 if ok == "ok" else 1)
PY

read -r -p "Replace the current studio with this backup? The current data is kept aside first. [yes/NO] " answer
[ "$answer" = "yes" ] || { echo "nothing changed"; exit 0; }

aside="$(dirname "$DATA")/pre-restore-$(date -u +%Y%m%d-%H%M%S)"
[ -n "${FILMLAB_NO_SERVICE:-}" ] || systemctl --user stop filmlab-backend || true
mkdir -p "$aside"
[ -d "$DATA" ] && mv "$DATA" "$aside/data"
[ -d "$MEDIA" ] && mv "$MEDIA" "$aside/media"
mkdir -p "$DATA" "$MEDIA"
# Keep the non-database files of data/ (probe image, older copies) from the current studio.
[ -d "$aside/data" ] && find "$aside/data" -maxdepth 1 -type f ! -name 'filmlab.sqlite*' -exec cp -p {} "$DATA/" \;
cp -p "$src/filmlab.sqlite" "$DATA/filmlab.sqlite"
rsync -a "$src/media/" "$MEDIA/"
echo "restored $(basename "$src"); the previous studio is in $aside"
[ -n "${FILMLAB_NO_SERVICE:-}" ] || bash "$REPO/scripts/filmlabctl.sh" restart-backend
