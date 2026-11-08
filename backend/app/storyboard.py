"""Storyboard: scenes, shot order and locks, grounded estimates, and the director's validated plan.

Order: scenes by position, the shots of each scene by position, then shots without a scene
("Unsorted"). shots.position is kept as that one sequence (1..n), so a shot's number is its place
in the film, and a batch renders in exactly that order.

plan_storyboard checks a whole plan before writing anything and then writes it in one transaction,
as drafts. A re-plan only replaces unlocked draft shots without takes.
"""

import json
import math
import re

from . import shots
from .capabilities import setting_error, setting_problem
from .sound import clean_sound
from .db import Database, new_id, now
from .events import EventBus
from .media import snap_frames

MAX_SCENES, MAX_SHOTS, MAX_REFS = 12, 30, 8
REPLACEABLE = ("draft", "pending_approval")


class StoryboardError(ValueError):
    pass


class PlanInvalid(StoryboardError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _text(value, limit: int, field: str, errors: list, required: bool = False, minimum: int = 0) -> str:
    value = " ".join(str(value or "").split()) if limit <= 200 else str(value or "").strip()
    if required and len(value) < max(1, minimum):
        errors.append(f"{field} is required" + (f" (at least {minimum} characters)" if minimum > 1 else ""))
    elif len(value) > limit:
        errors.append(f"{field} must be at most {limit} characters")
    return value[:limit]


class Storyboard:
    def __init__(self, db: Database, bus: EventBus, library, caps, renders):
        self.db, self.bus, self.library, self.caps, self.renders = db, bus, library, caps, renders

    # --- reading ---------------------------------------------------------

    async def scenes(self, project_id: str) -> list[dict]:
        return await self.db.fetchall("SELECT * FROM scenes WHERE project_id = ? AND deleted_at IS NULL ORDER BY position",
                                      (project_id,))

    async def order(self, project_id: str) -> list[str]:
        """Shot ids in storyboard order."""
        scene_pos = {s["id"]: s["position"] for s in await self.scenes(project_id)}
        rows = await self.db.fetchall("SELECT id, scene_id, position FROM shots WHERE project_id = ? AND deleted_at IS NULL",
                                      (project_id,))
        rows.sort(key=lambda r: (scene_pos.get(r["scene_id"], math.inf), r["position"]))
        return [r["id"] for r in rows]

    async def renumber(self, project_id: str, order: list[str] | None = None):
        order = order or await self.order(project_id)

        def write(c):
            for i, sid in enumerate(order, start=1):
                c.execute("UPDATE shots SET position = ? WHERE id = ?", (i, sid))
        await self.db.transaction(write)

    async def rates(self) -> dict:
        """Seconds of WanGP generation per frame, per model, from this machine's own past takes. Preview takes
        count apart, under "<model_type>:preview"."""
        rows = await self.db.fetchall("SELECT t.provenance, t.preview, m.frames FROM takes t JOIN media m ON m.id = t.media_id "
                                      "WHERE t.provenance IS NOT NULL")
        samples: dict[str, list[float]] = {}
        for r in rows:
            p = json.loads(r["provenance"])
            if p.get("generation_s") and p.get("model_type") and r["frames"]:
                key = p["model_type"] + (":preview" if r["preview"] else "")
                samples.setdefault(key, []).append(p["generation_s"] / r["frames"])
        return {k: {"s_per_frame": sum(v) / len(v), "samples": len(v)} for k, v in samples.items()}

    async def estimate(self, shot_rows: list[dict]) -> dict:
        """GPU time from past renders of the same model; shots of a model never rendered here aren't guessed."""
        rates = await self.rates()
        total, covered, based_on = 0.0, 0, 0
        for s in shot_rows:
            r = rates.get(s["model_type"])
            if r:
                total += r["s_per_frame"] * s["frames"]
                covered += 1
                based_on += r["samples"]
        return {"seconds": round(total) if covered else None, "shots": len(shot_rows), "covered": covered,
                "based_on": based_on}

    # --- scenes ----------------------------------------------------------

    def _scene_fields(self, fields: dict, errors: list, partial: bool) -> dict:
        out = {}
        if "title" in fields or not partial:
            out["title"] = _text(fields.get("title"), 120, "title", errors, required=True)
        for k, lim in (("goal", 1000), ("time_place", 200), ("mood", 200)):
            if k in fields:
                out[k] = _text(fields[k], lim, k, errors)
        return out

    async def create_scene(self, project_id: str, fields: dict, actor: str) -> dict:
        errors: list[str] = []
        data = self._scene_fields(fields, errors, partial=False)
        if errors:
            raise StoryboardError("; ".join(errors))
        loc = await self._location(fields.get("location_asset_id"))
        pos = await self.db.fetchone("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM scenes WHERE project_id = ?",
                                     (project_id,))
        sid, ts = new_id(), now()
        await self.db.execute(
            "INSERT INTO scenes (id, project_id, position, title, goal, time_place, mood, location_asset_id, created_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, project_id, pos["p"], data["title"], data.get("goal", ""), data.get("time_place", ""),
             data.get("mood", ""), loc, actor, ts, ts))
        await self.db.audit(actor, "scene.create", sid)
        await self.bus.publish("shot.updated", {"scene_id": sid}, project_id)
        return await self.scene(sid)

    async def scene(self, scene_id: str, project_id: str | None = None) -> dict:
        s = await self.db.fetchone("SELECT * FROM scenes WHERE id = ? AND deleted_at IS NULL", (scene_id,))
        if not s or (project_id and s["project_id"] != project_id):
            raise StoryboardError("no such scene in this project")
        return s

    async def _location(self, asset_id) -> str | None:
        if not asset_id:
            return None
        a = await self.db.fetchone("SELECT id FROM assets WHERE id = ? AND deleted_at IS NULL", (str(asset_id),))
        if not a:  # a location's name instead of its id ("jim_lab", "Jim lab"): accept it when exactly one matches
            key = re.sub(r"[\s_-]+", " ", str(asset_id)).strip().lower()
            rows = await self.db.fetchall("SELECT id, name FROM assets WHERE kind = 'location' AND deleted_at IS NULL AND draft = 0")
            hits = [r for r in rows if re.sub(r"[\s_-]+", " ", r["name"]).strip().lower() == key]
            a = hits[0] if len(hits) == 1 else None
        if not a:
            raise StoryboardError("location_asset_id isn't a library location (use the id from list_assets)")
        return a["id"]

    async def update_scene(self, scene_id: str, fields: dict, actor: str, project_id: str | None = None) -> dict:
        s = await self.scene(scene_id, project_id)
        errors: list[str] = []
        data = self._scene_fields(fields, errors, partial=True)
        if errors:
            raise StoryboardError("; ".join(errors))
        if "location_asset_id" in fields:
            data["location_asset_id"] = await self._location(fields["location_asset_id"])
        if data:
            sets = ", ".join(f"{k} = ?" for k in data)
            await self.db.execute(f"UPDATE scenes SET {sets}, updated_at = ? WHERE id = ?", (*data.values(), now(), scene_id))
            await self.db.audit(actor, "scene.update", scene_id, sorted(data))
            await self.bus.publish("shot.updated", {"scene_id": scene_id}, s["project_id"])
        return await self.scene(scene_id)

    async def move_scene(self, scene_id: str, index: int, actor: str) -> list[dict]:
        s = await self.scene(scene_id)
        ids = [x["id"] for x in await self.scenes(s["project_id"]) if x["id"] != scene_id]
        ids.insert(max(0, min(int(index), len(ids))), scene_id)

        def write(c):
            for i, sid in enumerate(ids, start=1):
                c.execute("UPDATE scenes SET position = ? WHERE id = ?", (i, sid))
        await self.db.transaction(write)
        await self.renumber(s["project_id"])
        await self.db.audit(actor, "scene.move", scene_id, {"index": index})
        await self.bus.publish("shot.updated", {"scene_id": scene_id}, s["project_id"])
        return await self.scenes(s["project_id"])

    async def delete_scene(self, scene_id: str, trash, actor: str) -> dict:
        """The scene goes, and its shots to Recently deleted (a restored shot comes back in Unsorted)."""
        s = await self.scene(scene_id)
        members = await self.db.fetchall("SELECT id FROM shots WHERE scene_id = ? AND deleted_at IS NULL", (scene_id,))
        for m in members:
            await trash.delete_shot(m["id"], actor)
        await self.db.execute("UPDATE scenes SET deleted_at = ?, updated_at = ? WHERE id = ?", (now(), now(), scene_id))
        await self.db.execute("UPDATE shots SET scene_id = NULL WHERE scene_id = ?", (scene_id,))
        await self.renumber(s["project_id"])
        await self.db.audit(actor, "scene.delete", scene_id, {"shots": len(members)})
        await self.bus.publish("shot.updated", {"scene_id": scene_id}, s["project_id"])
        return {"deleted": "scene", "id": scene_id, "shots_deleted": len(members)}

    # --- shots: move and lock --------------------------------------------

    async def move_shot(self, shot_id: str, scene_id: str | None, index: int | None, actor: str,
                        project_id: str | None = None) -> dict:
        shot = await shots.get_shot(self.db, shot_id, project_id)
        if actor == "director" and shot["locked"]:
            raise StoryboardError(f"shot {shot['position']} is locked by the owner")
        if scene_id:
            await self.scene(scene_id, shot["project_id"])
        order = await self.order(shot["project_id"])
        order.remove(shot_id)
        rows = {r["id"]: r for r in await self.db.fetchall("SELECT id, scene_id FROM shots WHERE project_id = ? "
                                                           "AND deleted_at IS NULL", (shot["project_id"],))}
        group = [sid for sid in order if rows[sid]["scene_id"] == (scene_id or None)]
        index = len(group) if index is None else max(0, min(int(index), len(group)))
        if group:  # insert next to the scene's shots, at the given place among them
            anchor = group[index - 1] if index > 0 else group[0]
            at = order.index(anchor) + (1 if index > 0 else 0)
        else:  # empty scene: after the last shot of an earlier scene
            at = len(order)
        order.insert(at, shot_id)
        await self.db.execute("UPDATE shots SET scene_id = ?, updated_at = ? WHERE id = ?", (scene_id or None, now(), shot_id))
        await self.renumber(shot["project_id"], order)
        await self.renumber(shot["project_id"])  # normalise: one 1..n sequence in scene order
        await self.db.audit(actor, "shot.move", shot_id, {"scene_id": scene_id, "index": index})
        await self.bus.publish("shot.updated", {"shot_id": shot_id}, shot["project_id"])
        return await shots.get_shot(self.db, shot_id)

    async def set_locked(self, shot_id: str, locked: bool, actor: str) -> dict:
        shot = await shots.get_shot(self.db, shot_id)
        await self.db.execute("UPDATE shots SET locked = ?, updated_at = ? WHERE id = ?", (int(locked), now(), shot_id))
        await self.db.audit(actor, "shot.lock" if locked else "shot.unlock", shot_id)
        await self.bus.publish("shot.updated", {"shot_id": shot_id}, shot["project_id"])
        return await shots.get_shot(self.db, shot_id)

    async def restore_revision(self, revision_id: str, actor: str) -> dict:
        """Put a shot back to how it was right after that revision (logged as a new revision)."""
        rev = await self.db.fetchone("SELECT * FROM shot_revisions WHERE id = ?", (revision_id,))
        if not rev:
            raise StoryboardError("no such revision")
        shot = await shots.get_shot(self.db, rev["shot_id"])
        if shot["locked"]:
            raise StoryboardError("the shot is locked; unlock it first")
        if shot["status"] not in shots.EDITABLE:
            raise StoryboardError(f"shot is {shot['status']}; wait until it isn't approved or rendering")
        target = json.loads(rev["after"])
        before = await shots.snapshot(self.db, shot["id"])
        fields = {k: target[k] for k in shots.TRACKED if k in target}
        tpl = await self.renders.resolve_template(fields.pop("template"))
        fields["model_type"] = tpl["model_type"]
        fields["template"] = tpl["template"]
        status = "ready" if shot["status"] == "ready" else "draft"
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.db.execute(f"UPDATE shots SET {sets}, status = ?, updated_at = ? WHERE id = ?",
                              (*fields.values(), status, now(), shot["id"]))
        await self.db.execute("UPDATE approvals SET status = 'withdrawn', decided_at = ? WHERE shot_id = ? "
                              "AND status = 'pending'", (now(), shot["id"]))
        if [dict(r) for r in before["refs"]] != target["refs"]:
            live = [r for r in target["refs"] if await self.db.fetchone(
                "SELECT 1 FROM variants v JOIN assets a ON a.id = v.asset_id JOIN variant_files vf ON vf.variant_id = v.id "
                "WHERE v.id = ? AND vf.file_id = ? AND v.deleted_at IS NULL AND a.deleted_at IS NULL",
                (r["variant_id"], r["file_id"]))]
            await self.db.execute("DELETE FROM shot_refs WHERE shot_id = ?", (shot["id"],))
            for i, r in enumerate(live, start=1):
                await self.db.execute("INSERT INTO shot_refs (shot_id, position, variant_id, file_id, role) "
                                      "VALUES (?, ?, ?, ?, ?)", (shot["id"], i, r["variant_id"], r["file_id"], r["role"]))
        await shots.record(self.db, shot["id"], actor, "revert", before,
                           f"restored the version from {rev['created_at'][:16].replace('T', ' ')}")
        await self.db.audit(actor, "shot.revert", shot["id"], {"revision": revision_id})
        await self.bus.publish("shot.updated", {"shot_id": shot["id"], "status": status}, shot["project_id"])
        return await shots.get_shot(self.db, shot["id"])

    # --- the director's plan ---------------------------------------------

    async def _image(self, file_id) -> dict | None:
        """The newest live look holding this library image."""
        return await self.db.fetchone(
            "SELECT v.id AS variant_id, vf.file_id, a.id AS asset_id, a.name, a.kind FROM variant_files vf "
            "JOIN variants v ON v.id = vf.variant_id JOIN assets a ON a.id = v.asset_id JOIN library_files f "
            "ON f.id = vf.file_id WHERE vf.file_id = ? AND f.kind = 'image' AND v.deleted_at IS NULL "
            "AND a.deleted_at IS NULL AND a.draft = 0 ORDER BY v.version DESC LIMIT 1", (str(file_id),))

    async def validate_plan(self, project_id: str, plan: dict) -> dict:
        """Check every scene and shot; return the normalised plan, or raise PlanInvalid with all errors."""
        errors: list[str] = []
        warnings: list[str] = []
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not isinstance(plan, dict):
            raise PlanInvalid(["the plan must be an object with a scenes list"])
        scenes_in = plan.get("scenes")
        if not isinstance(scenes_in, list) or not scenes_in:
            raise PlanInvalid(["scenes must be a non-empty list"])
        if len(scenes_in) > MAX_SCENES:
            errors.append(f"at most {MAX_SCENES} scenes")
        existing = {s["id"]: s for s in await self.scenes(project_id)}
        out_scenes, total_shots, total_s = [], 0, 0.0
        for i, sc in enumerate(scenes_in[:MAX_SCENES]):
            where = f"scenes[{i}]"
            if not isinstance(sc, dict):
                errors.append(f"{where} must be an object")
                continue
            e = []
            scene = {"scene_id": None, "fields": self._scene_fields(sc, e, partial=bool(sc.get("scene_id")))}
            errors += [f"{where}: {x}" for x in e]
            if sc.get("scene_id"):
                if sc["scene_id"] not in existing:
                    errors.append(f"{where}.scene_id isn't a scene of this project")
                scene["scene_id"] = sc["scene_id"]
            if sc.get("location_asset_id"):
                try:
                    scene["location"] = await self._location(sc["location_asset_id"])
                except StoryboardError as exc:
                    errors.append(f"{where}: {exc}")
            shots_in = sc.get("shots")
            if not isinstance(shots_in, list) or not shots_in:
                errors.append(f"{where}.shots must be a non-empty list")
                shots_in = []
            scene["shots"] = []
            for j, sh in enumerate(shots_in):
                total_shots += 1
                w = f"{where}.shots[{j}]"
                if not isinstance(sh, dict):
                    errors.append(f"{w} must be an object")
                    continue
                e: list[str] = []
                shot = {"title": _text(sh.get("title"), 120, "title", e, required=True),
                        "idea": _text(sh.get("idea"), 1000, "idea", e),
                        "prompt": _text(sh.get("prompt"), 4000, "prompt", e, required=True, minimum=10),
                        "rationale": _text(sh.get("rationale"), 1000, "rationale", e, required=True)}
                transition = sh.get("transition") or "cut"
                if transition not in shots.TRANSITIONS:
                    e.append(f"transition must be one of {', '.join(shots.TRANSITIONS)}")
                shot["transition"] = transition
                shot["sound"] = clean_sound(sh.get("sound"), sound_errors := [])
                e += sound_errors
                try:
                    seconds = float(sh.get("seconds") or 0) or (project["default_frames"] - 1) / 24
                except (TypeError, ValueError):
                    e.append("seconds must be a number")
                    seconds = 5.0
                if not 0.5 <= seconds <= 20:
                    e.append("seconds must be 0.5-20")
                shot["frames"] = snap_frames(round(seconds * 24) + 1)
                total_s += (shot["frames"] - 1) / 24
                if sh.get("resolution"):
                    try:
                        shot["resolution"] = shots.clean_fields({"resolution": sh["resolution"]}, partial=True)["resolution"]
                    except shots.ShotError as exc:
                        e.append(str(exc))
                refs_in = sh.get("refs") or []
                if not isinstance(refs_in, list):
                    e.append("refs must be a list of image file_ids")
                    refs_in = []
                if len(refs_in) > MAX_REFS:
                    e.append(f"at most {MAX_REFS} refs")
                refs = []
                for k, fid in enumerate(refs_in[:MAX_REFS]):
                    fid = fid.get("file_id") if isinstance(fid, dict) else fid
                    img = await self._image(fid)
                    if not img:
                        e.append(f"refs[{k}] '{fid}' isn't a library image (use images[].file_id from get_asset)")
                    else:
                        refs.append(img)
                shot["refs"] = refs
                place = scene["fields"].get("time_place") or (existing.get(scene["scene_id"]) or {}).get("time_place") or ""
                # the place and time of day: checked even when the shot has other errors, so one reply lists them all
                if (problem := setting_problem(shot["prompt"], place)) if shot.get("prompt") else None:
                    e.append(setting_error(problem))
                if not e:
                    tpl = await self.caps.effective_template({"template_explicit": 0}, project, len(refs))
                    shot["template"] = tpl
                    plan_refs = [{"position": k, "asset_id": r["asset_id"], "asset_name": r["name"], "kind": r["kind"]}
                                 for k, r in enumerate(refs, start=1)]
                    for issue in await self.caps.checks(tpl, len(refs), shot["prompt"], place, sound_fields=shot["sound"],
                                                        refs=plan_refs, voiced=[]):
                        if issue["level"] == "info":
                            continue
                        if issue.get("kind") == "setting":  # already checked above
                            continue
                        # A prompt naming a picture the shot doesn't have is an error in a plan, not a hint.
                        blocking = issue["level"] == "block" or issue.get("kind") == "picture"
                        (e if blocking else warnings).append(issue["message"] if blocking else f"{w}: {issue['message']}")
                errors += [f"{w}: {x}" for x in e]
                scene["shots"].append(shot)
            out_scenes.append(scene)
        if total_shots > MAX_SHOTS:
            errors.append(f"at most {MAX_SHOTS} shots")
        if errors:
            raise PlanInvalid(errors)
        target = project.get("target_seconds")
        if target and not 0.5 * target <= total_s <= 1.5 * target:
            warnings.append(f"the plan runs {total_s:.0f} s against a target of {target} s")
        return {"logline": _text(plan.get("logline"), 300, "logline", []), "scenes": out_scenes,
                "replace_drafts": bool(plan.get("replace_drafts")), "warnings": warnings,
                "total_seconds": round(total_s, 1), "shot_count": total_shots}

    async def apply_plan(self, project_id: str, plan: dict, actor: str) -> dict:
        """Validate, then write the whole plan in one transaction as drafts."""
        p = await self.validate_plan(project_id, plan)
        templates = {}
        for sc in p["scenes"]:
            for sh in sc["shots"]:
                if sh["template"] not in templates:
                    templates[sh["template"]] = await self.renders.resolve_template(sh["template"])
        project = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        live = await self.db.fetchall(
            "SELECT s.id, s.position, s.title, s.status, s.locked, s.scene_id, "
            "(SELECT COUNT(*) FROM takes t WHERE t.shot_id = s.id) AS takes FROM shots s "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL ORDER BY s.position", (project_id,))
        replaced = [s for s in live if p["replace_drafts"] and s["status"] in REPLACEABLE and not s["locked"]
                    and not s["takes"]] if p["replace_drafts"] else []
        kept = [s for s in live if s not in replaced]
        ts = now()
        created_scenes, created_shots, revisions = [], [], []
        scene_pos = (await self.db.fetchone("SELECT COALESCE(MAX(position), 0) AS p FROM scenes WHERE project_id = ?",
                                            (project_id,)))["p"]
        shot_pos = max([s["position"] for s in kept], default=0)

        def write(c):
            nonlocal scene_pos, shot_pos
            for s in replaced:
                c.execute("UPDATE shots SET deleted_at = ?, updated_at = ? WHERE id = ?", (ts, ts, s["id"]))
                c.execute("UPDATE approvals SET status = 'withdrawn', decided_at = ? WHERE shot_id = ? "
                          "AND status = 'pending'", (ts, s["id"]))
            for sc in p["scenes"]:
                if sc["scene_id"]:
                    sid = sc["scene_id"]
                    if sc["fields"]:
                        sets = ", ".join(f"{k} = ?" for k in sc["fields"])
                        c.execute(f"UPDATE scenes SET {sets}, updated_at = ? WHERE id = ?", (*sc["fields"].values(), ts, sid))
                else:
                    sid, scene_pos = new_id(), scene_pos + 1
                    f = sc["fields"]
                    c.execute("INSERT INTO scenes (id, project_id, position, title, goal, time_place, mood, "
                              "location_asset_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (sid, project_id, scene_pos, f["title"], f.get("goal", ""), f.get("time_place", ""),
                               f.get("mood", ""), sc.get("location"), actor, ts, ts))
                    created_scenes.append(sid)
                for sh in sc["shots"]:
                    shot_id, shot_pos = new_id(), shot_pos + 1
                    tpl = templates[sh["template"]]
                    c.execute(
                        "INSERT INTO shots (id, project_id, position, title, description, prompt, template, model_type, "
                        "resolution, frames, seed, status, created_by, created_at, updated_at, template_explicit, idea, "
                        "scene_id, transition, rationale, sound) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, -1, 'draft', ?, ?, "
                        "?, 0, ?, ?, ?, ?, ?)",
                        (shot_id, project_id, shot_pos, sh["title"], sh["prompt"], tpl["template"], tpl["model_type"],
                         sh.get("resolution") or project["default_resolution"], sh["frames"], actor, ts, ts,
                         sh["idea"], sid, sh["transition"], sh["rationale"],
                         json.dumps(sh["sound"]) if sh["sound"] is not None else None))
                    for k, r in enumerate(sh["refs"], start=1):
                        c.execute("INSERT INTO shot_refs (shot_id, position, variant_id, file_id, role) VALUES (?, ?, ?, ?, '')",
                                  (shot_id, k, r["variant_id"], r["file_id"]))
                        c.execute("INSERT OR IGNORE INTO project_cast (project_id, asset_id, added_at) VALUES (?, ?, ?)",
                                  (project_id, r["asset_id"], ts))
                    after = {"title": sh["title"], "idea": sh["idea"], "prompt": sh["prompt"], "frames": sh["frames"],
                             "resolution": sh.get("resolution") or project["default_resolution"],
                             "template": tpl["template"], "template_explicit": 0, "seed": -1,
                             "transition": sh["transition"],
                             "sound": json.dumps(sh["sound"]) if sh["sound"] is not None else None,
                             "refs": [{"variant_id": r["variant_id"], "file_id": r["file_id"], "role": ""}
                                      for r in sh["refs"]]}
                    c.execute("INSERT INTO shot_revisions (id, shot_id, actor, kind, before, after, rationale, created_at) "
                              "VALUES (?, ?, ?, 'plan', '{}', ?, ?, ?)",
                              (new_id(), shot_id, actor, json.dumps(after), sh["rationale"], ts))
                    created_shots.append(shot_id)
            if p["logline"]:
                c.execute("UPDATE projects SET logline = ?, updated_at = ? WHERE id = ?", (p["logline"], ts, project_id))
        await self.db.transaction(write)
        await self.renumber(project_id)
        await self.db.audit(actor, "storyboard.plan", project_id, {"scenes": len(created_scenes), "shots": len(created_shots),
                                                                   "replaced": [s["id"] for s in replaced]})
        await self.bus.publish("shot.updated", {"planned": True}, project_id)
        return {"created_scenes": len(created_scenes), "created_shots": created_shots, "warnings": p["warnings"],
                "total_seconds": p["total_seconds"],
                "replaced": [f"shot {s['position']} “{s['title']}”" for s in replaced],
                "kept": [f"shot {s['position']} “{s['title']}” ({'locked' if s['locked'] else s['status']}"
                         f"{', has takes' if s['takes'] else ''})" for s in kept]}
