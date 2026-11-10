"""Library references by name, so the director never copies an image id or keeps <Picture N> numbers in step.

In a prompt, {Heavy_Metal_Antihero} or {Heavy_Metal_Antihero/front portrait} (a view's caption, or its number: /2)
names a library asset. FilmLab attaches that asset's image (its main look; the named view, else the first image),
numbers the pictures in order of first mention, and writes <Picture N> in their place. The same asset and view named
twice is one picture. A small model's slips are repaired where the intent is clear (a name one letter off, an asset or
look id or a cut-short image id given as a ref) and the repair is reported back; anything unclear is an error with
suggestions.
"""

import difflib
import re

from .db import Database

TOKEN = re.compile(r"\{([^{}<>\n]{2,160})\}")
CLOSE = 0.88  # a name this close to exactly one asset is taken as that asset
MIN_PREFIX = 8


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(s or "")).strip().lower()


async def _assets(db: Database) -> list[dict]:
    return await db.fetchall("SELECT id, name, kind, created_at FROM assets WHERE deleted_at IS NULL AND draft = 0")


async def _images(db: Database, asset_id: str) -> list[dict]:
    """The asset's images in its main look: the look named "default" if it has images, else the first by name."""
    rows = await db.fetchall(
        "SELECT v.id AS variant_id, v.name AS look, v.version, vf.file_id, vf.caption, vf.position FROM variants v "
        "JOIN variant_files vf ON vf.variant_id = v.id JOIN library_files f ON f.id = vf.file_id "
        "WHERE v.asset_id = ? AND v.deleted_at IS NULL AND f.kind = 'image' ORDER BY v.name, v.version DESC, vf.position",
        (asset_id,))
    looks: dict[str, list[dict]] = {}
    for r in rows:
        looks.setdefault(r["variant_id"], []).append(r)
    if not looks:
        return []
    main = next((imgs for imgs in looks.values() if imgs[0]["look"].lower() == "default"), next(iter(looks.values())))
    return main


def _ref(asset: dict, img: dict) -> dict:
    return {"variant_id": img["variant_id"], "file_id": img["file_id"], "asset_id": asset["id"], "name": asset["name"],
            "kind": asset["kind"], "caption": img.get("caption") or ""}


async def find_asset(db: Database, name: str, allowed: set[str] | None = None) -> tuple[dict | None, str | None, list[str]]:
    """(asset, repair note, suggestions) for a name as the director wrote it; among `allowed` assets if given."""
    assets = [a for a in await _assets(db) if allowed is None or a["id"] in allowed]
    want = _norm(name)
    exact = [a for a in assets if _norm(a["name"]) == want]
    if exact:
        return exact[0], None, []
    names = {_norm(a["name"]): a for a in assets}
    scored = sorted(((difflib.SequenceMatcher(None, want, n).ratio(), n) for n in names), reverse=True)
    close = [n for r, n in scored if r >= CLOSE]
    if len(close) == 1:
        a = names[close[0]]
        return a, f"{{{name}}} read as {a['name']}", []
    return None, None, [names[n]["name"] for r, n in scored[:3] if r >= 0.5]


async def pick_image(db: Database, asset: dict, view: str | None) -> tuple[dict | None, str | None]:
    imgs = await _images(db, asset["id"])
    if not imgs:
        return None, f"{asset['name']} has no image yet"
    if not view:
        return _ref(asset, imgs[0]), None
    v = view.strip()
    if v.isdigit() and 1 <= int(v) <= len(imgs):
        return _ref(asset, imgs[int(v) - 1]), None
    want = _norm(v)
    hit = next((i for i in imgs if _norm(i["caption"]) == want), None) or \
        next((i for i in imgs if want and want in _norm(i["caption"])), None)
    if hit:
        return _ref(asset, hit), None
    views = ", ".join(i["caption"] or str(n) for n, i in enumerate(imgs, start=1))
    return _ref(asset, imgs[0]), f"{asset['name']} has no view “{v}” (views: {views}); used its first image"


async def resolve_prompt(db: Database, prompt: str, max_refs: int, allowed: set[str] | None = None) -> dict | None:
    """None when the prompt names no asset; else {prompt, refs, notes, errors}: refs in picture order."""
    tokens = list(TOKEN.finditer(prompt or ""))
    if not tokens:
        return None
    if re.search(r"<Picture\s*\d+>", prompt):
        return {"prompt": prompt, "refs": [], "notes": [], "errors": [
            "the prompt mixes {asset names} and <Picture N>: use one way (names are easier: FilmLab numbers them)"]}
    refs: list[dict] = []
    index: dict[tuple[str, str], int] = {}
    notes, errors = [], []
    out, last = [], 0
    for m in tokens:
        raw = m.group(1).strip()
        name, _, view = raw.partition("/")
        asset, note, suggest = await find_asset(db, name, allowed)
        if not asset:
            hint = f"; did you mean {', '.join('{' + s + '}' for s in suggest)}?" if suggest else ""
            where = "in this film's cast" if allowed is not None else "a library asset"
            errors.append(f"{{{raw}}} isn't {where}{hint} (name assets exactly as the list shows them)")
            continue
        if note:
            notes.append(note)
        img, vnote = await pick_image(db, asset, view or None)
        if vnote:
            (errors if img is None else notes).append(vnote)
        if img is None:
            continue
        key = (img["asset_id"], img["file_id"])
        if key not in index:
            refs.append(img)
            index[key] = len(refs)
        out.append(prompt[last:m.start()] + f"<Picture {index[key]}>")
        last = m.end()
    out.append(prompt[last:])
    if len(refs) > max_refs:
        errors.append(f"the prompt names {len(refs)} different pictures; at most {max_refs} per shot")
    return {"prompt": "".join(out), "refs": refs, "notes": notes, "errors": errors}


_STOP = {"the", "a", "an", "of", "in", "on", "at", "and", "to", "near", "with", "from", "into", "toward", "towards"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in _STOP]


async def infer_place(db: Database, allowed: set[str], text: str) -> dict | None:
    """The one cast location a scene's description clearly names, else None. Words every cast name shares (the
    film's prefix, e.g. "heavy metal") don't count; a name word counts when the text has it inside a longer word
    ("ship" in "spaceship"). The best needs two matched words, half its name, and no tie."""
    cast = await db.fetchall(f"SELECT id, name, kind FROM assets WHERE id IN ({','.join('?' * len(allowed))}) "
                             "AND deleted_at IS NULL", tuple(allowed)) if allowed else []
    places = [a for a in cast if a["kind"] == "location"]
    if not places or not text.strip():
        return None
    every = {w for a in cast for w in _words(a["name"])}  # from all names: the first may be a typo ("Heavy_Netal")
    common = {w for w in every if sum(w in _words(a["name"]) for a in cast) > len(cast) / 2}
    said = _words(text)
    scored = []
    for p in places:
        own = [w for w in _words(p["name"]) if w not in common] or _words(p["name"])
        hit = sum(1 for w in own if any(w == s or (len(w) >= 4 and w in s) for s in said))
        scored.append((hit, hit / len(own), p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = scored[0]
    if best[0] < 2 or best[1] < 0.5 or (len(scored) > 1 and scored[1][:2] == best[:2]):
        return None
    return best[2]


async def lenient_image(db: Database, value) -> tuple[dict | None, str | None]:
    """A ref as the director gave it: an image file_id, or (repaired, with a note) an asset or look id, an image id cut
    short, or an asset name. None when nothing matches clearly."""
    v = str(value or "").strip().strip("{}")
    if not v:
        return None, None
    row = await db.fetchone(
        "SELECT v.id AS variant_id, vf.file_id, a.id AS asset_id, a.name, a.kind, vf.caption FROM variant_files vf "
        "JOIN variants v ON v.id = vf.variant_id JOIN assets a ON a.id = v.asset_id JOIN library_files f ON f.id = vf.file_id "
        "WHERE vf.file_id = ? AND f.kind = 'image' AND v.deleted_at IS NULL AND a.deleted_at IS NULL AND a.draft = 0 "
        "ORDER BY v.version DESC LIMIT 1", (v,))
    if row:
        return row, None
    asset = await db.fetchone("SELECT id, name, kind FROM assets WHERE id = ? AND deleted_at IS NULL AND draft = 0", (v,))
    if not asset:
        look = await db.fetchone("SELECT a.id, a.name, a.kind FROM variants v JOIN assets a ON a.id = v.asset_id "
                                 "WHERE v.id = ? AND v.deleted_at IS NULL AND a.deleted_at IS NULL AND a.draft = 0", (v,))
        asset = look
    if asset:
        img, _ = await pick_image(db, asset, None)
        return (img, f"'{v}' is {asset['name']}'s id, not an image id: used its first image") if img else (None, None)
    if re.fullmatch(r"[0-9a-f]{%d,31}" % MIN_PREFIX, v):
        rows = await db.fetchall(
            "SELECT DISTINCT vf.file_id FROM variant_files vf JOIN library_files f ON f.id = vf.file_id "
            "WHERE f.kind = 'image' AND vf.file_id LIKE ?", (v + "%",))
        if len(rows) == 1:
            img, _ = await lenient_image(db, rows[0]["file_id"])
            if img:
                return img, f"'{v}' was cut short: read as {rows[0]['file_id']} ({img['name']})"
    asset, note, _ = await find_asset(db, v.split("/")[0])
    if asset:
        img, _ = await pick_image(db, asset, v.partition("/")[2] or None)
        if img:
            return img, f"'{v}' read as {asset['name']}'s image"
    return None, None
