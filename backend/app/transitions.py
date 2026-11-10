"""Plan transitions: the director picks the transition (and its length) for each cut of the edit, from the mood of
the shots on either side. It's a chat turn, so the owner watches it think; the director's propose_transitions call
only stores a proposal. Nothing changes until the owner reviews the list and applies it (Edit view), which sets
each shot's transition (shots.set_transition) and the clip's transition length in the edit."""

import asyncio
import logging

from . import shots
from .db import now

log = logging.getLogger("filmlab.transitions")

SCOPES = ("scenes", "all")
MAX_S = 3.0

GUIDE = """How to choose (pick what serves the story, not variety for its own sake):
- cut: the default inside a scene, for continuous action, dialogue and fast, punchy moments. Most cuts stay cuts.
- dissolve: calm, gentle passage of time, related images, a quiet or tender mood. 0.6-1.5 s.
- fade (through black): the end of a scene or chapter, a time jump, something solemn, dark or final. 0.8-1.5 s.
- fade_white: dream, memory, flashback, revelation, a blinding flash, something heavenly or hopeful. 0.6-1.2 s.
- wipe_* / slide_*: energetic, playful, comic-book or retro pacing; direction follows the movement. 0.3-0.6 s.
- soft_wipe_*: a gentler move from one place to another. 0.5-0.9 s.
- circle_open / circle_close: storybook or cartoon iris in/out (opening or closing a chapter). 0.6-1.0 s.
- clock_wipe: time passing. 0.8-1.2 s.
- zoom_in: sudden impact, rushing into the next moment, action. 0.2-0.4 s.
- blur: disorientation, waking up, dizziness, a dream starting. 0.5-0.9 s.
- pixelize: digital, glitch, video-game worlds. 0.3-0.6 s.
- squeeze: comic, quirky. 0.3-0.5 s.
Keep the film consistent: two or three kinds of transition at most, used the same way each time (e.g. every scene
change a fade through black), unless the story clearly asks for something else. Seconds never exceed the cut's max_s."""


def _secs(frames: int, fps: int) -> float:
    return round(frames / fps, 2)


class TransitionPlanner:
    def __init__(self, studio):
        self.s = studio
        self.plans: dict[str, dict] = {}  # project -> the latest plan (in memory: a restart drops an unapplied one)
        self._tasks: set[asyncio.Task] = set()

    async def cuts(self, project_id: str) -> list[dict]:
        """Every cut between two clips of the edit, in film order, with what the director needs to judge it."""
        tl = await self.s.edit.timeline(project_id)
        fps = tl["fps"]
        scenes = {sc["id"]: sc for sc in await self.s.storyboard.scenes(project_id)}
        segs = tl["segments"]
        clips = [(i, g) for i, g in enumerate(segs) if g["kind"] == "clip"]
        out = []
        for (i, a), (j, b) in zip(clips, clips[1:]):
            sa = await shots.get_shot(self.s.db, a["shot_id"])
            sb = await shots.get_shot(self.s.db, b["shot_id"])
            da, db_ = a["frames"] / fps, b["frames"] / fps
            sca, scb = scenes.get(sa["scene_id"]), scenes.get(sb["scene_id"])
            current_s = (tl["doc"]["clips"].get(a["shot_id"]) or {}).get("transition_s", tl["doc"]["settings"]["transition_s"])
            out.append({
                "shot_id": a["shot_id"], "to_shot_id": b["shot_id"],
                "from": self._side(sa, sca, da), "to": self._side(sb, scb, db_),
                "scene_change": (sa["scene_id"] or "") != (sb["scene_id"] or ""),
                "title_between": j != i + 1,
                "current": {"transition": sa["transition"] or "cut", "seconds": round(float(current_s), 2)},
                "max_s": round(min(MAX_S, da / 2, db_ / 2), 2),
            })
        return out

    @staticmethod
    def _side(shot: dict, scene: dict | None, seconds: float) -> dict:
        return {"position": shot["position"], "title": shot["title"], "idea": (shot.get("idea") or shot["prompt"] or "")[:240],
                "seconds": round(seconds, 2), "scene": scene["title"] if scene else None,
                "scene_mood": (scene or {}).get("mood") or None}

    def plan(self, project_id: str) -> dict | None:
        return self.plans.get(project_id)

    async def start(self, project_id: str, scope: str, note: str = "") -> dict:
        if scope not in SCOPES:
            raise TransitionError(f"scope must be one of {', '.join(SCOPES)}")
        cuts = await self.cuts(project_id)
        if not cuts:
            raise TransitionError("the edit needs at least two clips with takes before there's a cut to plan")
        chosen = [c for c in cuts if c["scene_change"]] if scope == "scenes" else cuts
        if not chosen:
            raise TransitionError("the film has no scene changes yet (every clip is in one scene): plan every cut instead")
        project = await self.s.db.fetchone("SELECT title, logline, brief, style, music_mode FROM projects WHERE id = ?",
                                           (project_id,))
        brief = self.brief(project, cuts, chosen, scope, note)
        plan = {"project_id": project_id, "scope": scope, "status": "thinking", "turn_id": None, "cuts": chosen,
                "items": [], "summary": "", "error": None, "created_at": now()}
        self.plans[project_id] = plan
        try:
            plan["turn_id"] = await self.s.chat.send(project_id, brief,
                                                     expect=("propose_transitions", "the transition plan"),
                                                     tools=["propose_transitions", "get_edit", "get_project"])
        except Exception:
            self.plans.pop(project_id, None)
            raise
        await self.s.db.audit("owner", "transitions.plan", project_id, {"scope": scope, "cuts": len(chosen)})
        await self._publish(project_id)
        task = asyncio.create_task(self._watch(project_id, plan))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return plan

    async def _watch(self, project_id: str, plan: dict):
        """When the director's turn ends without a proposal, the modal says so instead of waiting forever."""
        try:
            await asyncio.sleep(0.05)
            await self.s.chat.wait(project_id)
            if self.plans.get(project_id) is plan and plan["status"] == "thinking":
                plan["status"], plan["error"] = "failed", "The director finished without proposing transitions. Try again."
                await self._publish(project_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("watching the transition plan failed")

    def brief(self, project: dict, cuts: list[dict], chosen: list[dict], scope: str, note: str) -> str:
        n = len(chosen)
        lines = [
            f"Plan the transitions of the edit: {'the ' + str(n) + ' scene change' + ('s' if n != 1 else '') if scope == 'scenes' else 'every cut (' + str(n) + ')'}.",
            "For each cut, choose the transition into the next shot and its length, from the mood and energy of the "
            "shots on either side: e.g. a calm moment eases out with a dissolve or a fade through black or white; "
            "action and shocks get a hard cut or a quick, dramatic transition.",
        ]
        if project.get("logline") or project.get("brief"):
            lines.append(f"The film: “{' '.join(str(project.get('logline') or project.get('brief')).split())[:400]}”")
        if project.get("style"):
            lines.append(f"Style: {' '.join(project['style'].split())[:300]}")
        if project.get("music_mode"):
            lines.append("This is a music video: every transition overlaps two shots, so it shortens the film against "
                         "the song. Keep them few and short.")
        if note:
            lines.append(f"My note: {' '.join(note.split())[:500]}")
        lines.append(f"The film has {len(cuts) + 1} clips. The cuts to plan, in film order:")
        for k, c in enumerate(chosen, 1):
            a, b = c["from"], c["to"]
            side = lambda x: (f"shot {x['position']} “{x['title']}” ({x['seconds']} s"  # noqa: E731
                              + (f", scene “{x['scene']}”" + (f", mood: {x['scene_mood']}" if x['scene_mood'] else "") if x['scene'] else "")
                              + f"): {x['idea']}")
            lines.append(f"{k}. shot_id {c['shot_id']}{' · SCENE CHANGE' if c['scene_change'] else ''}"
                         f"{' · a title card comes between' if c['title_between'] else ''} · now: {c['current']['transition']}"
                         f"{'' if c['current']['transition'] == 'cut' else ' ' + str(c['current']['seconds']) + ' s'} · max_s {c['max_s']}\n"
                         f"   from {side(a)}\n   to {side(b)}")
        lines += [
            GUIDE,
            "First say in two or three short sentences how you read the film's rhythm and what your approach is. Then "
            "call propose_transitions once, with one entry per cut above (shot_id = the shot the transition leaves), "
            "a transition, seconds (0 for a cut) and a short reason the owner will read. It only proposes: I review "
            "and apply them. Don't change any shot or the edit with other tools.",
        ]
        return "\n".join(lines)

    async def propose(self, project_id: str, transitions, summary: str = "") -> dict:
        """The director's propose_transitions: checked against the plan's cuts and stored for the owner."""
        plan = self.plans.get(project_id)
        if not plan or plan["status"] == "applied":
            cuts = await self.cuts(project_id)
            if not cuts:
                raise ValueError("the edit has no cuts yet (it needs two clips with takes)")
            plan = {"project_id": project_id, "scope": "all", "status": "thinking", "turn_id": None, "cuts": cuts,
                    "items": [], "summary": "", "error": None, "created_at": now()}
            self.plans[project_id] = plan
        if not isinstance(transitions, list) or not transitions:
            raise ValueError("transitions must be a list with one entry per cut")
        by_id = {c["shot_id"]: c for c in plan["cuts"]}
        errors, picked = [], {}
        for n, t in enumerate(transitions):
            if not isinstance(t, dict):
                errors.append(f"transitions[{n}] must be an object")
                continue
            sid, kind = str(t.get("shot_id") or ""), str(t.get("transition") or "")
            if sid not in by_id:
                errors.append(f"transitions[{n}]: shot_id {sid or '(none)'} isn't one of the cuts to plan")
                continue
            if kind not in shots.TRANSITIONS:
                errors.append(f"transitions[{n}]: transition must be one of {', '.join(shots.TRANSITIONS)}")
                continue
            try:
                secs = float(t.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0.0
            cut = by_id[sid]
            secs = 0.0 if kind == "cut" else round(min(max(secs or 0.5, 0.1), cut["max_s"]), 2)
            picked[sid] = {"transition": kind, "seconds": secs, "reason": " ".join(str(t.get("reason") or "").split())[:300]}
        if errors:
            raise ValueError("; ".join(errors[:8]))
        items, kept = [], []
        for c in plan["cuts"]:
            p = picked.get(c["shot_id"])
            if not p:
                kept.append(c["from"]["position"])
                p = {**c["current"], "reason": "(the director left this one as it is)"}
            items.append({"shot_id": c["shot_id"], "to_shot_id": c["to_shot_id"], "from": c["from"], "to": c["to"],
                          "scene_change": c["scene_change"], "max_s": c["max_s"], "current": c["current"], **p})
        plan.update(items=items, summary=" ".join(str(summary or "").split())[:600], status="ready", error=None)
        await self._publish(project_id)
        out = {"proposed": len(items), "message": "The owner reviews these in the Edit view; nothing changes until "
                                                  "they apply them. Reply with a one-line summary."}
        return out | ({"kept_as_is": kept, "note": f"shots {kept} had no entry, so they keep their transition"} if kept else {})

    async def apply(self, project_id: str, items: list[dict], actor: str) -> dict:
        live = {r["id"] for r in await self.s.db.fetchall(
            "SELECT id FROM shots WHERE project_id = ? AND deleted_at IS NULL", (project_id,))}
        plan = self.plans.get(project_id) or {}
        reasons = {i["shot_id"]: i.get("reason") or "" for i in plan.get("items") or []}
        clips, changed = {}, 0
        for it in items:
            sid, kind = str(it.get("shot_id") or ""), str(it.get("transition") or "")
            if sid not in live:
                raise TransitionError(f"shot {sid} isn't in this project")
            if kind not in shots.TRANSITIONS:
                raise TransitionError(f"transition must be one of {', '.join(shots.TRANSITIONS)}")
        for it in items:
            sid, kind = str(it["shot_id"]), str(it["transition"])
            shot = await shots.get_shot(self.s.db, sid)
            why = reasons.get(sid)
            before = shot["transition"]
            await shots.set_transition(self.s.db, self.s.bus, shot, kind, actor,
                                       f"planned transition{': ' + why if why else ''}")
            changed += before != kind
            if kind != "cut":
                secs = round(min(max(float(it.get("seconds") or 0.5), 0.05), MAX_S), 2)
                clips[sid] = {"transition_s": secs}
        if clips:
            tl = await self.s.edit.update(project_id, {"clips": clips}, actor,
                                          f"applied planned transitions ({len(items)} cut{'s' if len(items) != 1 else ''})")
        else:
            tl = await self.s.edit.timeline(project_id)
        if plan:
            plan["status"] = "applied"
        self.plans.pop(project_id, None)
        await self.s.db.audit(actor, "transitions.apply", project_id, {"cuts": len(items), "changed": changed})
        await self._publish(project_id)
        return tl

    async def discard(self, project_id: str) -> bool:
        gone = self.plans.pop(project_id, None) is not None
        if gone:
            await self._publish(project_id)
        return gone

    async def _publish(self, project_id: str):
        await self.s.bus.publish("transitions.plan", {"status": (self.plans.get(project_id) or {}).get("status")},
                                 project_id)

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()


class TransitionError(ValueError):
    pass
