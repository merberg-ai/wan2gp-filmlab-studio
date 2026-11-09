"""✨ Write the story: the director writes the Storybook's narration, a short passage for each page (picture book) or
a caption for each panel (comic), and may suggest a title and subtitle. It's a chat turn the owner watches; its
propose_story call only stores a proposal, which the owner edits and accepts in the Storybook (or discards)."""

import asyncio
import logging

from .db import now

log = logging.getLogger("filmlab.storybook")


class WriterError(ValueError):
    pass


class StoryWriter:
    def __init__(self, studio):
        self.s = studio
        self.plans: dict[str, dict] = {}
        self._tasks: set[asyncio.Task] = set()

    def plan(self, project_id: str) -> dict | None:
        return self.plans.get(project_id)

    async def start(self, project_id: str, note: str = "") -> dict:
        view = await self.s.storybook.view(project_id)
        panels = [p for p in view["panels"] if not p["skip"]]
        if not panels:
            raise WriterError("the book has no shots to write for")
        project = await self.s.db.fetchone("SELECT title, logline, brief, style FROM projects WHERE id = ?", (project_id,))
        brief = self.brief(project, view, panels, note)
        plan = {"status": "thinking", "turn_id": None, "items": [], "title": "", "subtitle": "", "summary": "",
                "error": None, "layout": view["doc"]["layout"], "created_at": now(),
                "shot_ids": [p["shot_id"] for p in panels]}
        self.plans[project_id] = plan
        try:
            plan["turn_id"] = await self.s.chat.send(project_id, brief, expect=("propose_story", "the story text"))
        except Exception:
            self.plans.pop(project_id, None)
            raise
        await self.s.db.audit("owner", "storybook.write", project_id, {"panels": len(panels)})
        await self._publish(project_id)
        task = asyncio.create_task(self._watch(project_id, plan))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return plan

    async def _watch(self, project_id: str, plan: dict):
        try:
            await asyncio.sleep(0.05)
            await self.s.chat.wait(project_id)
            if self.plans.get(project_id) is plan and plan["status"] == "thinking":
                plan["status"], plan["error"] = "failed", "The director finished without writing the story. Try again."
                await self._publish(project_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("watching the story writer failed")

    @staticmethod
    def brief(project: dict, view: dict, panels: list[dict], note: str) -> str:
        comic = view["doc"]["layout"] == "comic"
        lines = [
            f"Write the story for this film's Storybook, a {'comic' if comic else 'picture book'} made from frames of "
            "its shots. " + (
                "Each panel gets a caption box: at most 20 words, present tense, what the picture can't say (time, "
                "place, a thought, what happens between panels). The characters' dialogue is already in speech "
                "bubbles, so never repeat it. A panel may have no caption (empty text) when the picture and bubbles "
                "say it all." if comic else
                "Each page has one picture and your narration under it: 1-3 sentences (at most 60 words) of storybook "
                "prose in past tense that carries the story from page to page. The characters' own lines are printed "
                "under it, so don't repeat them; you may lead into them."),
            "Write in a voice that fits the film's style and audience; keep names consistent; let each page follow "
            "from the last, as one story.",
        ]
        if project.get("logline") or project.get("brief"):
            lines.append(f"The film: “{' '.join(str(project.get('logline') or project.get('brief')).split())[:500]}”")
        if project.get("style"):
            lines.append(f"Style: {' '.join(project['style'].split())[:300]}")
        if note:
            lines.append(f"My note: {' '.join(note.split())[:500]}")
        lines.append(f"The {'panels' if comic else 'pages'}, in order:")
        scene = object()
        for p in panels:
            if p["scene_id"] != scene and p["scene"]:
                lines.append(f"[Scene “{p['scene']}”{', mood: ' + p['scene_mood'] if p['scene_mood'] else ''}]")
            scene = p["scene_id"]
            talk = "; ".join(f"{ln['speaker'] or 'someone'}: “{ln['text']}”" for ln in p["lines"]) or "none"
            lines.append(f"- shot_id {p['shot_id']} · shot {p['position']} “{p['title']}”: {' '.join(p['idea'].split())[:300]}"
                         f" · dialogue: {talk}" + (f" · current text: “{p['narration']}”" if p["narration"] else ""))
        lines.append("First say in a sentence or two how you'll tell it. Then call propose_story once with one entry per "
                     "shot above (shot_id and text), and a title and subtitle for the cover if you have better ones "
                     f"than “{view['doc']['cover']['title']}”. It only proposes: I edit and accept it. Don't change any "
                     "shot with other tools.")
        return "\n".join(lines)

    async def propose(self, project_id: str, pages, title: str = "", subtitle: str = "", summary: str = "") -> dict:
        plan = self.plans.get(project_id)
        if not plan or plan["status"] not in ("thinking", "ready", "failed"):
            raise ValueError("propose_story is for when the owner asks you to write the Storybook (✨ Write the story)")
        if not isinstance(pages, list) or not pages:
            raise ValueError("pages must be a list with one entry per shot")
        allowed = set(plan["shot_ids"])
        texts, errors = {}, []
        for n, p in enumerate(pages):
            sid = str((p or {}).get("shot_id") or "") if isinstance(p, dict) else ""
            if sid not in allowed:
                errors.append(f"pages[{n}]: shot_id {sid or '(none)'} isn't one of the shots listed")
                continue
            texts[sid] = " ".join(str(p.get("text") or "").split())[:600]
        if errors:
            raise ValueError("; ".join(errors[:8]))
        view = await self.s.storybook.view(project_id)
        by_id = {p["shot_id"]: p for p in view["panels"]}
        plan["items"] = [{"shot_id": sid, "position": by_id[sid]["position"], "title": by_id[sid]["title"],
                          "take_id": by_id[sid]["take_id"], "t": by_id[sid]["t"], "crop": by_id[sid]["crop"],
                          "current": by_id[sid]["narration"], "text": texts.get(sid, by_id[sid]["narration"]),
                          "lines": by_id[sid]["lines"]}
                         for sid in plan["shot_ids"] if sid in by_id]
        plan.update(status="ready", error=None, title=" ".join(str(title or "").split())[:120],
                    subtitle=" ".join(str(subtitle or "").split())[:200], summary=" ".join(str(summary or "").split())[:400])
        await self._publish(project_id)
        missing = [by_id[s]["position"] for s in plan["shot_ids"] if s not in texts and s in by_id]
        out = {"proposed": len(texts), "message": "The owner edits and accepts it in the Storybook; nothing changed "
                                                 "yet. Reply with a one-line summary."}
        return out | ({"note": f"shots {missing} had no entry; they keep their text"} if missing else {})

    async def apply(self, project_id: str, texts: dict[str, str], title: str | None, subtitle: str | None,
                    actor: str) -> dict:
        patch: dict = {"panels": {sid: {"narration": str(t or "")} for sid, t in texts.items()}}
        cover = {k: v for k, v in (("title", title), ("subtitle", subtitle)) if v is not None}
        if cover:
            patch["cover"] = cover
        view = await self.s.storybook.update(project_id, patch, actor, f"wrote the story ({len(texts)} pages)")
        self.plans.pop(project_id, None)
        await self._publish(project_id)
        return view

    async def discard(self, project_id: str) -> bool:
        gone = self.plans.pop(project_id, None) is not None
        if gone:
            await self._publish(project_id)
        return gone

    async def _publish(self, project_id: str):
        await self.s.bus.publish("storybook.write", {"status": (self.plans.get(project_id) or {}).get("status")},
                                 project_id)

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()
