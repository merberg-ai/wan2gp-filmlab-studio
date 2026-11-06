"""Model presets: a named template + size + length + how to write for it, for shots.

FilmLab ships presets for the templates it was tuned on (MiniMax Ref2VA, LTX-2) and adds a plain one
for every other installed WanGP video template. The owner can save their own. A preset is usable only
when its template is valid and fully installed in WanGP: FilmLab never installs or downloads models,
so a preset whose model files are missing says so instead of applying.
"""

from . import shots
from .capabilities import VIDEO_TASKS
from .db import new_id, now
from .media import seconds_to_frames, snap_frames

KV = "presets.custom"
MAX_CUSTOM = 30

REF_TIPS = ("Name every reference in the prompt as <Picture 1>, <Picture 2> in their order; characters first, then "
            "the location. The model makes the sound too: say “No dialogue, no voices” or give the exact line in quotes.")
BUILTIN = [
    {"id": "minimax-character", "name": "Character shot", "template": "MiniMax H3 Ref2VA Pruned PDD 8 Steps",
     "resolution": "832x480", "seconds": 5, "refs": "1-3 references",
     "use": "A recurring character in a known place: the references keep faces, costume and location consistent.",
     "tips": REF_TIPS},
    {"id": "minimax-beat", "name": "Quick beat", "template": "MiniMax H3 Ref2VA Pruned PDD 8 Steps",
     "resolution": "832x480", "seconds": 3, "refs": "1-2 references",
     "use": "A short reaction, insert or cutaway with a known character or prop.",
     "tips": "One action only: a 3-second shot has no room for two. " + REF_TIPS},
    {"id": "minimax-omni", "name": "Character shot, Omni", "template": "MiniMax H3 FastH3 Omni 8 Steps",
     "resolution": "832x480", "seconds": 5, "refs": "1-3 references",
     "use": "Like Character shot, with MiniMax's Omni model.", "tips": REF_TIPS},
    {"id": "minimax-plain", "name": "No-reference shot (MiniMax)", "template": "MiniMax H3 FL2VA Pruned PDD 8 Steps",
     "resolution": "832x480", "seconds": 5, "refs": "none",
     "use": "A shot with no library references, rendered from the prompt alone.",
     "tips": "State the setting, light and weather in full: without references the model knows only the prompt. "
             "Say “No dialogue, no voices” unless someone speaks."},
    {"id": "ltx-establishing", "name": "Establishing wide", "template": "LTX-2 2.3 Distilled 1.0",
     "resolution": "1280x720", "seconds": 5, "refs": "none or 1 start image",
     "use": "Scenery, weather and places without close character work, at 720p.",
     "tips": "Describe the place, time of day, weather and camera move in one flowing paragraph; LTX-2 follows "
             "camera directions well (slow push in, drone pull back). Describe the sound and say if no one speaks."},
    {"id": "ltx-long", "name": "Long take", "template": "LTX-2 2.3 Distilled 1.0",
     "resolution": "1280x720", "seconds": 8, "refs": "none or 1 start image",
     "use": "A slow, continuous move or a longer moment at 720p.",
     "tips": "Give the shot a beginning, middle and end; long takes drift when the prompt describes one static moment."},
    {"id": "ltx-msr-refs", "name": "Character shot, 720p", "template": "LTX-2 2.3 MSR V2 Distilled 1.1",
     "resolution": "1280x720", "seconds": 5, "refs": "1-3 references",
     "use": "Reference-guided shots at 720p with LTX-2's multi-reference model.", "tips": REF_TIPS},
]
BUILTIN_IDS = {p["id"] for p in BUILTIN}


class PresetError(ValueError):
    pass


def _status(entry: dict | None) -> tuple[str, str]:
    if not entry:
        return "unavailable", "this template isn't in WanGP"
    if entry["valid"] is False:
        return "unavailable", entry.get("reason") or "WanGP rejects this template"
    if entry.get("installed") is False:
        return "needs_download", ("its model files aren't fully downloaded in WanGP. FilmLab doesn't download models: "
                                  "download it in WanGP first, then refresh the capabilities in Settings")
    return "ready", ""


class Presets:
    def __init__(self, db, caps, renders):
        self.db, self.caps, self.renders = db, caps, renders

    async def custom(self) -> list[dict]:
        return list(await self.db.kv_get(KV, []) or [])

    async def listing(self) -> list[dict]:
        matrix = [t for t in await self.caps.matrix() if t["task"] in VIDEO_TASKS]
        by_name = {t["template"]: t for t in matrix}
        out = [dict(p, source="filmlab") for p in BUILTIN]
        covered = {p["template"] for p in BUILTIN}
        for t in matrix:  # every other installed template gets a plain preset with WanGP's own defaults
            if t["template"] in covered or t["valid"] is False or t.get("installed") is False:
                continue
            covered.add(t["template"])
            out.append({"id": f"wangp:{t['template']}", "name": t["template"], "template": t["template"],
                        "resolution": t.get("default_resolution") or "832x480", "seconds": 5,
                        "refs": {"multiple": "references", "single": "none or 1 reference"}.get(t["refs"], "none"),
                        "use": "WanGP's defaults for this template.", "tips": "", "source": "wangp"})
        out += [dict(p, source="owner") for p in await self.custom()]
        for p in out:
            entry = by_name.get(p["template"])
            p["status"], p["reason"] = _status(entry)
            p["frames"] = p.get("frames") or seconds_to_frames(p["seconds"])
            p["seconds"] = round((p["frames"] - 1) / 24, 2)
            p["audio"] = bool(entry and entry.get("audio"))
            p["takes_refs"] = bool(entry and entry["refs"] != "none")
            p["fps"] = entry and entry.get("fps")
        order = {"ready": 0, "needs_download": 1, "unavailable": 2}
        return sorted(out, key=lambda p: (order[p["status"]], p["source"] == "owner", 0))

    async def get(self, preset_id: str) -> dict:
        p = next((p for p in await self.listing() if p["id"] == preset_id), None)
        if not p:
            raise PresetError(f"no preset {preset_id!r}; list_presets shows them")
        return p

    async def create(self, fields: dict, actor: str) -> dict:
        name = str(fields.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise PresetError("name must be 1-60 characters")
        template = str(fields.get("template") or "")
        matrix = await self.caps.matrix()
        self.caps.ensure_usable(matrix, template, VIDEO_TASKS)
        try:
            clean = shots.clean_fields({"resolution": fields.get("resolution"), "frames": fields.get("frames")}, True)
        except shots.ShotError as exc:
            raise PresetError(str(exc)) from exc
        if "resolution" not in clean or "frames" not in clean:
            raise PresetError("a preset needs a size (e.g. 832x480) and a length in frames")
        custom = await self.custom()
        if len(custom) >= MAX_CUSTOM:
            raise PresetError(f"at most {MAX_CUSTOM} presets of your own; delete one first")
        if any(p["name"].lower() == name.lower() for p in custom):
            raise PresetError(f"you already have a preset called {name!r}")
        p = {"id": f"own-{new_id()[:10]}", "name": name, "template": template, "resolution": clean["resolution"],
             "frames": snap_frames(clean["frames"]), "seconds": 0, "refs": "", "created_at": now(),
             "use": str(fields.get("use") or "").strip()[:300], "tips": str(fields.get("tips") or "").strip()[:600]}
        await self.db.kv_set(KV, custom + [p])
        await self.db.audit(actor, "preset.create", p["id"], {"name": name, "template": template})
        return await self.get(p["id"])

    async def delete(self, preset_id: str, actor: str) -> dict:
        if preset_id in BUILTIN_IDS or preset_id.startswith("wangp:"):
            raise PresetError("FilmLab's own presets can't be deleted")
        custom = await self.custom()
        kept = [p for p in custom if p["id"] != preset_id]
        if len(kept) == len(custom):
            raise PresetError("no such preset")
        await self.db.kv_set(KV, kept)
        await self.db.audit(actor, "preset.delete", preset_id)
        return {"deleted": preset_id}

    async def apply(self, shot: dict, preset_id: str, actor: str, keep_length: bool = False,
                    rationale: str = "") -> dict:
        """Set a shot's template, size and (unless keep_length) length from a preset."""
        p = await self.get(preset_id)
        if p["status"] != "ready":
            raise PresetError(f"preset {p['name']!r} can't be used: {p['reason']}")
        tpl = await self.renders.resolve_template(p["template"])
        fields = {"resolution": p["resolution"]} | ({} if keep_length else {"frames": p["frames"]})
        return await shots.update_shot(self.db, self.renders.bus, shot, fields, actor, tpl,
                                       rationale=rationale or f"preset “{p['name']}”")
