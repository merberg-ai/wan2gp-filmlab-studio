"""Context-aware library search: the library ranked for one shot (or scene) of a project.

Signals, strongest first: the asset is named in the shot; it is the scene's location; other shots of
the same scene use it (continuity, and which look they use); it is in the project's cast; it is used
elsewhere in the project; its description and tags share words with the shot and scene. Each result
says why it was suggested and which look to use, so the owner (or the director) can judge it.
"""

import re
from collections import Counter

STOP = set("""
a an and are as at be but by for from has have he her his in into is it its of on or she so that the their them then
there they this to was were will with without while who whom where when what which near over under out up down off
very just also only than too not no nor yet our your you we us me my him one two three all any each some more most
shot shots scene scenes camera picture pictures frame frames close wide medium slow slowly fast quick moment moments
looking looks look toward towards behind front back side left right still static beat beats begin begins end ends
dialogue voices voice sound sounds audio speech spoken silent quiet ambient film style mood light lighting tone
best good great make makes making get gets like wear wears wearing worn has had does did being been
""".split())
NAMED, LOCATION, SCENE_USE, CAST, PROJECT_USE, WORD, WORD_CAP = 6.0, 5.0, 4.0, 2.5, 1.0, 0.6, 3.0


def words(*texts: str) -> list[str]:
    text = re.sub(r"<\s*picture\s+\d+\s*>", " ", " ".join(t or "" for t in texts), flags=re.I).lower()
    return [w for w in re.findall(r"[a-z][a-z'-]{2,}", text) if w not in STOP]


def _stem(w: str) -> str:
    for suf in ("ing", "ies", "es", "s", "ed", "y"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _named(name: str, text: str) -> bool:
    name = re.sub(r"[_\-]+", " ", name.lower()).strip()  # "Anthony_PJ" is named by "Anthony"
    if not name:
        return False
    if re.search(rf"\b{re.escape(name)}\b", text):
        return True
    first = name.split()[0]  # "Anthony Reyes" is named by "Anthony"
    return len(first) >= 4 and first not in STOP and re.search(rf"\b{re.escape(first)}\b", text) is not None


async def suggest(db, library, project_id: str, shot_id: str | None = None, scene_id: str | None = None,
                  query: str = "", kind: str | None = None, limit: int = 200) -> dict:
    shot = None
    if shot_id:
        shot = await db.fetchone("SELECT * FROM shots WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                                 (shot_id, project_id))
        if not shot:
            raise ValueError("no such shot in this project")
        scene_id = scene_id or shot["scene_id"]
    scene = scene_id and await db.fetchone("SELECT * FROM scenes WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                                           (scene_id, project_id))
    project = await db.fetchone("SELECT title, logline FROM projects WHERE id = ?", (project_id,))
    shot_text = " ".join((shot or {}).get(k) or "" for k in ("title", "idea", "prompt", "description"))
    scene_text = " ".join((scene or {}).get(k) or "" for k in ("title", "goal", "time_place", "mood"))
    all_words = words(shot_text, scene_text, (project or {}).get("logline") or "")
    context = Counter(_stem(w) for w in all_words)
    spelled = {}
    for w in all_words:  # show the word as written, not its stem
        spelled.setdefault(_stem(w), w)
    text_lower = re.sub(r"\s+", " ", f"{shot_text} {scene_text}".lower())

    candidates = await library.search(query, kind, limit=500)
    if not candidates:
        return {"context": _context_view(shot, scene, context, spelled), "assets": []}
    ids = [a["id"] for a in candidates]
    marks = ",".join("?" * len(ids))
    texts = {r["asset_id"]: r["text"] for r in await db.fetchall(
        f"SELECT asset_id, text FROM library_search WHERE asset_id IN ({marks})", tuple(ids))}
    cast = {r["asset_id"] for r in await db.fetchall("SELECT asset_id FROM project_cast WHERE project_id = ?", (project_id,))}
    uses = await db.fetchall(
        "SELECT v.asset_id, sr.variant_id, sr.file_id, s.id AS shot_id, s.scene_id, s.position, v.name, v.version "
        "FROM shot_refs sr JOIN shots s ON s.id = sr.shot_id JOIN variants v ON v.id = sr.variant_id "
        "WHERE s.project_id = ? AND s.deleted_at IS NULL AND v.deleted_at IS NULL ORDER BY s.position", (project_id,))
    variants = await db.fetchall(
        f"SELECT v.id, v.asset_id, v.name, v.version, v.locked, (SELECT vf.file_id FROM variant_files vf JOIN "
        f"library_files f ON f.id = vf.file_id WHERE vf.variant_id = v.id AND f.kind = 'image' ORDER BY vf.position "
        f"LIMIT 1) AS file_id FROM variants v WHERE v.deleted_at IS NULL AND v.asset_id IN ({marks}) "
        f"ORDER BY v.locked DESC, v.version DESC, v.created_at DESC", tuple(ids))
    on_shot = {u["asset_id"] for u in uses if shot and u["shot_id"] == shot["id"]}

    out = []
    for a in candidates:
        score, reasons = 0.0, []
        if _named(a["name"], text_lower):
            score += NAMED
            reasons.append("named in the shot" if shot else "named in the scene")
        if scene and scene["location_asset_id"] == a["id"]:
            score += LOCATION
            reasons.append("the scene's location")
        mine = [u for u in uses if u["asset_id"] == a["id"] and not (shot and u["shot_id"] == shot["id"])]
        in_scene = [u for u in mine if scene and u["scene_id"] == scene["id"]]
        if in_scene:
            score += SCENE_USE
            n = len({u["shot_id"] for u in in_scene})
            reasons.append(f"used in {n} other shot{'s' if n != 1 else ''} of this scene")
        if a["id"] in cast:
            score += CAST
            reasons.append("in the cast")
        if mine and not in_scene:
            score += PROJECT_USE
            reasons.append("used elsewhere in this film")
        asset_words = {_stem(w) for w in words(texts.get(a["id"], ""), re.sub(r"[_\-]+", " ", a["name"]))}
        shared = [w for w in context if w in asset_words]
        if shared:
            score += min(WORD_CAP, WORD * sum(context[w] for w in shared))
            reasons.append("matches " + ", ".join(spelled[w] for w in sorted(shared, key=lambda w: -context[w])[:4]))
        look = _suggest_look(a["id"], in_scene, mine, variants)
        out.append({k: a[k] for k in ("id", "kind", "name", "description", "tags", "cover_file_id", "variant_count")}
                   | {"score": round(score, 2), "reasons": reasons, "on_shot": a["id"] in on_shot, "look": look})
    out.sort(key=lambda r: (-r["score"], r["kind"], r["name"].lower()))
    return {"context": _context_view(shot, scene, context, spelled), "assets": out[:limit]}


def _suggest_look(asset_id: str, in_scene: list, mine: list, variants: list) -> dict | None:
    """Which look to use: the one this scene already uses, else the film's, else a locked look, else the newest."""
    for pool, why in ((in_scene, "the look this scene uses"), (mine, "the look this film uses")):
        if pool:
            u = Counter((u["variant_id"], u["file_id"]) for u in pool).most_common(1)[0][0]
            v = next((v for v in variants if v["id"] == u[0]), None)
            if v:
                return {"variant_id": v["id"], "name": v["name"], "version": v["version"], "file_id": u[1], "why": why}
    for v in variants:
        if v["asset_id"] == asset_id and v["file_id"]:
            return {"variant_id": v["id"], "name": v["name"], "version": v["version"], "file_id": v["file_id"],
                    "why": "locked look" if v["locked"] else "newest look"}
    return None


def _context_view(shot, scene, context: Counter, spelled: dict | None = None) -> dict:
    spelled = spelled or {}
    return {"shot": shot and shot["title"], "scene": scene and scene["title"],
            "words": [spelled.get(w, w) for w, _ in context.most_common(8)]}
