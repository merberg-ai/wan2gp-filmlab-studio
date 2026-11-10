"""Who and where: after a plan part is saved in a film with a cast, FilmLab asks the model one small structured
question per new shot ("which of these cast members are in it, and which place?") and attaches the answers as the
shot's references. A small director writes good descriptions but forgets {names} (live: 13 shots, no character
attached), and a scene's one place is wrong for shots that move around inside it; picking from a list, forced into a
JSON shape by Ollama, is what such a model does reliably.

Kept: references the director attached itself. Added: the characters, props and styles it names (in order, after
those), then the place last as the setting; at most MAX_PICTURES in all. The prompt gets a short line naming the new
pictures, so the video model uses them. Nothing changes when the answer adds nothing, or when the model can't be
reached (the plan stands as written)."""

import json
import logging

import httpx

from . import refnames, shots
from .storyboard import cast_ids

log = logging.getLogger("filmlab.castpass")
MAX_PICTURES = 4
NONE = "none"


def _schema(people: list[str], places: list[str]) -> dict:
    return {"type": "object", "required": ["characters", "place"], "properties": {
        "characters": {"type": "array", "items": {"type": "string", "enum": people or [NONE]}},
        "place": {"type": "string", "enum": [*places, NONE]}}}


def question(project: dict, shot: dict, scene: dict | None, ref_names: list[str], people: list[dict],
             places: list[dict]) -> str:
    def lines(items):
        return [f"- {a['name']}: {' '.join(str(a.get('description') or '').split())[:140]}" for a in items] or ["- (none)"]
    prompt = str(shot.get("prompt") or "")
    for n, name in enumerate(ref_names, start=1):
        prompt = prompt.replace(f"<Picture {n}>", name)
    return "\n".join([
        f"A shot of the film “{project['title']}”.",
        *([f"Scene: {scene['title']}" + (f" ({scene['time_place']})" if scene.get("time_place") else "")] if scene else []),
        f"Shot: {shot['title']}",
        *([f"What happens: {shot['idea']}"] if shot.get("idea") else []),
        f"Picture: {prompt}",
        "Cast members (characters, props, styles):", *lines(people),
        "Places:", *lines(places),
        "Which cast members are visible in this shot, and which place is it set in? Name only those the shot clearly "
        "shows (a character the shot describes, e.g. “the antihero”, counts). Place: the one where the shot happens, "
        f"or {NONE} if none of the places fits (e.g. deep space, a warp tunnel).",
    ])


async def ask(url: str, model: str, text: str, schema: dict, transport=None) -> dict | None:
    body = {"model": model.removeprefix("ollama/"), "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": 8192}, "messages": [{"role": "user", "content": text}]}
    try:
        async with httpx.AsyncClient(base_url=url, timeout=120, transport=transport) as c:
            r = await c.post("/api/chat", json=body)
            r.raise_for_status()
            return json.loads(r.json()["message"]["content"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("who-and-where question failed: %s", exc)
        return None


async def who_and_where(studio, project_id: str, shot_ids: list[str], model: str | None) -> list[str]:
    """Attach the cast members and place each new shot shows; returns notes for the director (and the chat)."""
    project = await studio.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    allowed = await cast_ids(studio.db, project) if project else None
    if not allowed or not shot_ids or not model:
        return []
    cast = await studio.db.fetchall(f"SELECT id, name, kind, description FROM assets WHERE id IN "
                                    f"({','.join('?' * len(allowed))}) AND deleted_at IS NULL AND draft = 0", tuple(allowed))
    people = [a for a in cast if a["kind"] != "location"]
    places = [a for a in cast if a["kind"] == "location"]
    by_name = {a["name"]: a for a in cast}
    schema = _schema([a["name"] for a in people], [a["name"] for a in places])
    url = (await studio.health.settings())["ollama_url"]
    scenes = {s["id"]: s for s in await studio.storyboard.scenes(project_id)}
    notes = []
    for sid in shot_ids:
        shot = await shots.get_shot(studio.db, sid)
        refs = await studio.library.shot_refs(sid)
        if len(refs) >= MAX_PICTURES:
            continue
        have = {r["asset_id"] for r in refs}
        has_place = any(r["kind"] == "location" for r in refs)
        answer = await ask(url, model, question(project, shot, scenes.get(shot["scene_id"]), [r["asset_name"] for r in refs],
                                                people, places), schema, getattr(studio.gpu_share, "_transport", None))
        if not answer:
            notes.append("who-and-where: the model didn't answer; the shots keep the references they were planned with")
            break
        add = [by_name[n] for n in dict.fromkeys(answer.get("characters") or []) if n in by_name and by_name[n]["id"] not in have]
        place = by_name.get(answer.get("place") or "")
        room = MAX_PICTURES - len(refs) - (1 if place and not has_place and place["id"] not in have else 0)
        add = add[:max(0, room)]
        if place and (has_place or place["id"] in have):
            place = None
        if not add and not place:
            continue
        new = []
        for a in [*add, *([place] if place else [])]:
            img, _ = await refnames.pick_image(studio.db, a, None)
            if img:
                new.append((a, img))
        if not new:
            continue
        before = [{"variant_id": r["variant_id"], "file_id": r["file_id"], "role": r["role"]} for r in refs]
        all_refs = before + [{"variant_id": img["variant_id"], "file_id": img["file_id"]} for _, img in new]
        why = "who and where: " + ", ".join(a["name"] for a, _ in new)
        try:
            await studio.tools.assign_refs(project_id, sid, all_refs, why)
            # Picture numbers from the references as attached (not counted beforehand), so the prompt always names
            # exactly the shot's pictures.
            pos = {r["file_id"]: r["position"] for r in await studio.library.shot_refs(sid)}
            people_line = ", ".join(f"<Picture {pos[img['file_id']]}> is {a['name'].replace('_', ' ')}"
                                    for a, img in new if a["kind"] != "location" and img["file_id"] in pos)
            place_line = next((f"Setting: <Picture {pos[img['file_id']]}>." for a, img in new
                               if a["kind"] == "location" and img["file_id"] in pos), "")
            prompt = " ".join(x for x in (str(shot["prompt"]).rstrip(), f"{people_line}." if people_line else "",
                                          place_line) if x)
            await studio.tools.update_shot(project_id, sid, prompt=prompt, rationale=why)
        except Exception as exc:  # noqa: BLE001 - one shot's trouble never fails the plan that's already saved
            log.warning("who-and-where on shot %s failed: %s", sid, exc)
            try:  # never leave pictures the prompt doesn't name
                if before:
                    await studio.tools.assign_refs(project_id, sid, before, "who and where: undone")
                else:
                    await studio.library.set_shot_refs(await shots.get_shot(studio.db, sid), [], "director",
                                                       "who and where: undone")
            except Exception:  # noqa: BLE001
                log.exception("couldn't undo who-and-where on shot %s", sid)
            notes.append(f"shot {shot['position']} “{shot['title']}”: couldn't add "
                         f"{', '.join(a['name'] for a, _ in new)} ({str(exc)[:160]})")
            continue
        notes.append(f"shot {shot['position']} “{shot['title']}”: added {', '.join(a['name'] for a, _ in new)}")
    return notes
