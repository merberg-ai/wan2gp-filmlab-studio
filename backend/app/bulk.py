"""Storyboard bulk actions: delete, lock, unlock or change several shots (and whole scenes) at once.

A bulk delete can be undone for a while: the shots come back from Recently deleted, the deleted scenes with them,
in the order they had. Each shot is changed exactly as its own button would (the same rules and the same history);
a shot that can't be changed is skipped with the reason, and the others go ahead.
"""

import time

from . import shots
from .db import new_id
from .trash import TrashError

UNDO_S = 600  # how long a bulk delete can be undone
ACTIONS = ("delete", "lock", "unlock", "set")
SETTABLE = ("template", "resolution", "frames")


class BulkError(ValueError):
    pass


async def run(s, project_id: str, shot_ids: list[str], scene_ids: list[str], action: str, fields: dict,
              actor: str) -> dict:
    if action not in ACTIONS:
        raise BulkError(f"action must be one of {', '.join(ACTIONS)}")
    live = {r["id"]: r for r in await s.db.fetchall("SELECT * FROM shots WHERE project_id = ? AND deleted_at IS NULL",
                                                    (project_id,))}
    scenes = {r["id"]: r for r in await s.storyboard.scenes(project_id)}
    if set(shot_ids) - set(live) or set(scene_ids) - set(scenes):
        raise BulkError("some shots or scenes aren't in this project (or were already deleted)")
    if not shot_ids and not scene_ids:
        raise BulkError("choose at least one shot or scene")
    # a scene stands for all of its shots
    picked = list(dict.fromkeys([*shot_ids, *(sid for sid, r in live.items() if r["scene_id"] in set(scene_ids))]))
    done, skipped = [], []

    def skip(sid, why):
        skipped.append({"shot_id": sid, "position": live[sid]["position"], "title": live[sid]["title"], "reason": why})

    if action == "delete":
        order = await s.storyboard.order(project_id)
        for sid in picked:
            if live[sid]["locked"]:
                skip(sid, "it's locked")
                continue
            try:
                await s.trash.delete_shot(sid, actor)
                done.append(sid)
            except TrashError as exc:
                skip(sid, str(exc))
        # a chosen scene goes once none of its shots stayed behind
        kept = {live[x["shot_id"]]["scene_id"] for x in skipped}
        gone_scenes = [sc for sc in scene_ids if sc not in kept]
        for sc in gone_scenes:
            await s.storyboard.delete_scene(sc, s.trash, actor)  # its shots are already in Recently deleted
        await s.storyboard.renumber(project_id)
        token = new_id()
        await s.db.kv_set(f"bulk.undo:{token}", {
            "project_id": project_id, "at": time.time(), "order": order, "scenes": gone_scenes,
            "shots": [{"id": sid, "scene_id": live[sid]["scene_id"]} for sid in done]})
        await s.db.audit(actor, "storyboard.bulk_delete", project_id, {"shots": done, "scenes": gone_scenes})
        return {"action": action, "done": done, "skipped": skipped, "scenes_deleted": gone_scenes,
                "undo": token if done or gone_scenes else None, "undo_s": UNDO_S}

    if action in ("lock", "unlock"):
        for sid in picked:
            await s.storyboard.set_locked(sid, action == "lock", actor)
            done.append(sid)
        return {"action": action, "done": done, "skipped": skipped}

    change = {k: v for k, v in (fields or {}).items() if k in SETTABLE and v not in (None, "")}
    follow = (fields or {}).get("template") == ""  # back to the configured defaults
    if not change and not follow:
        raise BulkError(f"say what to change: {', '.join(SETTABLE)}")
    shots.clean_fields({k: v for k, v in change.items() if k != "template"}, partial=True)  # bad input: refused, not skipped
    template = None
    if "template" in change:
        template = await s.renders.resolve_template(change.pop("template"))
    elif follow:
        template = (await s.new_shot_template(project_id, None))[0]
    for sid in picked:
        shot = live[sid]
        if shot["locked"]:
            skip(sid, "it's locked")
            continue
        try:
            await shots.update_shot(s.db, s.bus, shot, dict(change), actor, template, follow_defaults=follow,
                                    rationale="changed with several shots at once")
            done.append(sid)
        except shots.ShotError as exc:
            skip(sid, str(exc))
    return {"action": action, "done": done, "skipped": skipped}


async def undo(s, project_id: str, token: str, actor: str) -> dict:
    key = f"bulk.undo:{token}"
    saved = await s.db.kv_get(key)
    if not saved or saved["project_id"] != project_id:
        raise BulkError("nothing to undo (it was already undone)")
    if time.time() - saved["at"] > UNDO_S:
        raise BulkError("too late to undo here; the shots are still in Recently deleted")
    for sc in saved["scenes"]:
        await s.db.execute("UPDATE scenes SET deleted_at = NULL WHERE id = ?", (sc,))
    restored = []
    for row in saved["shots"]:
        try:
            await s.trash.restore("shot", row["id"], actor)
        except TrashError:
            continue  # restored or purged from Recently deleted meanwhile
        await s.db.execute("UPDATE shots SET scene_id = ? WHERE id = ? AND (? IS NULL OR ? IN "
                           "(SELECT id FROM scenes WHERE deleted_at IS NULL))",
                           (row["scene_id"], row["id"], row["scene_id"], row["scene_id"]))
        restored.append(row["id"])
    # back in the order they had: the saved order first, then anything added since
    now_live = await s.storyboard.order(project_id)
    order = [x for x in saved["order"] if x in now_live] + [x for x in now_live if x not in saved["order"]]
    await s.storyboard.renumber(project_id, order)
    await s.db.execute("DELETE FROM kv WHERE key = ?", (key,))
    await s.db.audit(actor, "storyboard.bulk_undo", project_id, {"shots": restored, "scenes": saved["scenes"]})
    await s.bus.publish("shot.updated", {"bulk": True}, project_id)
    return {"restored": restored, "scenes": saved["scenes"]}
